"""文件读写的**呈现层**：对齐 OpenCode `tool/read.ts` / `write.ts` / `edit.ts`。

为什么单独一层：文件工具真正的价值不在"能读到内容"，而在**模型与人都能据此判断
下一步做什么**。裸内容有个致命问题——模型不知道自己看到的是全量还是半截，
人看不懂改了哪里。这里把两者都补上：

给模型（`output`）
    带行号的分段内容 + **明确的续读提示**（还有多少 / 从第几行继续）。
    没有这条，模型只能靠猜，读到半个大文件就以为自己读完了。

给人（`display`）
    结构化载荷（路径 / 行号区间 / 总行数 / 是否截断），前端据此渲染
    带行号的文件视图，而不是把一段 JSON 塞进折叠区让人自己读。

对齐的具体行为（均可在 `services/agent_zero/tests/test_file_ops.py` 复核）：

| 行为 | OpenCode | 这里 |
|---|---|---|
| 行号 | `1: 内容`（cat -n） | 同 |
| 分段读 | `offset` / `limit`，默认 2000 行 | 同 |
| 续读提示 | 三种：capped / more / end-of-file | 同 |
| 行内超长 | 截到 2000 字 + 显式后缀 | 同 |
| 总量封顶 | 50 KB | 同 |
| 文件不存在 | 模糊匹配兄弟文件，给 top 3「你是否想找」 | 同 |
| 二进制 | 扩展名 + NUL 字节 + 不可打印占比 > 30% | 同 |
| 写入回执 | 成功语 + **diff** | 同（并区分新建/覆盖） |
| 编辑 | oldString 必须唯一，报错可执行 | 同 |

本模块**只做纯计算**，不碰文件系统（路径校验、读写都在 workspace_service），
所以能被直接单测。
"""
from __future__ import annotations

import difflib
import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

# html/svg 写入回执附带完整内容供前端预览（有上限，避免 trace 膨胀）
PREVIEW_RE = re.compile(r"\.(html|htm|svg)$", re.I)
PREVIEW_MAX_CHARS = 12000

# ── 读取上限（对齐 OpenCode tool/read.ts）──────────────────────────────
READ_DEFAULT_LINES = 2000
READ_MAX_LINE_CHARS = 2000
READ_MAX_LINE_SUFFIX = f"... (line truncated to {READ_MAX_LINE_CHARS} chars)"
READ_MAX_BYTES = 50 * 1024
READ_MAX_BYTES_LABEL = f"{READ_MAX_BYTES // 1024} KB"

# ── 展示层上限（我们自己的，OpenCode 没有这层）────────────────────────
# display 会随 trace 落库（一个 JSON 字段），不能把整份大文件塞进去：
# 够看清「改了哪一段」即可，真要看全文走工作区文件页。
DISPLAY_MAX_LINES = 400
DISPLAY_MAX_CHARS = 12000

# diff 展示上限：超过这个行数只给统计，避免撑爆时间线
DIFF_MAX_LINES = 200

# 二进制扩展名（对齐 OpenCode isBinaryFile 的 switch）。
# 注意：Office / PDF 仍然在这里——它们不是"文本"；但 file_tools.exec_read
# 先走 document_service 的文本抽取，所以模型能读它们，只有其它链路（如
# 直接按文本读）才会被这里的判定拦住。
BINARY_EXTS = frozenset({
    ".zip", ".tar", ".gz", ".exe", ".dll", ".so", ".class", ".jar", ".war",
    ".7z", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods",
    ".odp", ".bin", ".dat", ".obj", ".o", ".a", ".lib", ".wasm", ".pyc", ".pyo",
    ".pdf", ".mp3", ".mp4", ".mov", ".avi", ".woff", ".woff2", ".ttf", ".eot",
})


class FileOpError(Exception):
    """文件操作错误——消息直接给模型看，必须是**可执行**的（说清下一步做什么）。"""


# ── 二进制判定 ────────────────────────────────────────────────────────
def is_binary(name: str, sample: bytes) -> bool:
    """是否二进制文件。

    两条判据（与 OpenCode 一致）：
      1. 已知二进制扩展名——直接判定，不看内容（.pdf 的头部和文本可能像）
      2. 采样里有 NUL 字节，或不可打印字符占比 > 30%

    为什么要单独判：此前 `errors="replace"` 兜底，等于把乱码当正文喂给模型，
    模型会基于乱码编出一段像模像样的解释——比直接报错危险得多。
    """
    ext = os.path.splitext(str(name or ""))[1].lower()
    if ext in BINARY_EXTS:
        return True
    if not sample:
        return False
    non_printable = 0
    for b in sample:
        if b == 0:
            return True
        if b < 9 or (13 < b < 32):
            non_printable += 1
    return non_printable / len(sample) > 0.3


# ── 「你是否想找」───────────────────────────────────────────────────────
# 相似度阈值：0.6。低于它给出的建议基本是噪音（`a.py` 匹配 `b.py` 那种），
# 高于它基本都是真拼错。0.6 是 difflib 默认 cutoff 的常用值，
# 实测 `aplication.py` → `application.py` 约 0.95，`main.py` → `domain.py` 约 0.55。
_CLOSE_CUTOFF = 0.6


def did_you_mean(names: Sequence[str], target: str, limit: int = 3) -> List[str]:
    """文件不存在时给出最像的几个兄弟条目。

    两级判据，**子串优先**：
      1. 双向子串（`a in b` 或 `b in a`，忽略大小写）——模型漏写目录段、
         漏写后缀、大小写乱写，这几类靠子串就能兜住，且最可信。
      2. 字符相似度（difflib）——**补子串兜不住的拼写错误**。
         模型写路径最常见的错就是掉一个字母（`aplication.py`），
         纯子串（OpenCode 的做法）对这类完全无能为力。

    为什么 2 放在 1 之后而不是只用相似度：相似度分不出「就是它」和「差不多」，
    而子串命中几乎可以确定就是同一条，顺序上必须优先。
    """
    t = str(target or "").strip().lower()
    if not t:
        return []
    hits = [n for n in names
            if t in str(n).lower() or str(n).lower() in t]
    if len(hits) < limit:
        seen = {str(h) for h in hits}
        for near in difflib.get_close_matches(
                t, [str(n).lower() for n in names], n=limit, cutoff=_CLOSE_CUTOFF):
            for n in names:
                if str(n).lower() == near and str(n) not in seen:
                    hits.append(n)
                    seen.add(str(n))
    # 短名优先（同名不同目录时先给最短路径）
    return sorted(hits, key=lambda n: (len(str(n)), str(n)))[:limit]


# ── 分段读取 ──────────────────────────────────────────────────────────
def slice_lines(text: str, *, offset: int = 1, limit: Optional[int] = None
                ) -> Dict[str, Any]:
    """按行号窗口切片，同时守「单行长度」与「总量字节」两道上限。

    返回 {raw, count, cut, more}：
      count = 文件总行数（读到哪儿算哪儿，但会一直数到底，好让模型知道全文多长）
      cut   = 撞到 50 KB 总量上限（后面还有内容，但这次只能到这里）
      more  = 撞到 limit 或字节上限
    """
    offset = max(1, int(offset or 1))
    limit = int(limit) if limit else READ_DEFAULT_LINES
    lines = text.split("\n")
    # 文件以换行结尾时 split 会多出一个空串，它不是真实的一行
    if lines and lines[-1] == "":
        lines.pop()

    start = offset - 1
    raw: List[str] = []
    used = 0
    cut = False
    for i in range(start, len(lines)):
        if len(raw) >= limit:
            return {"raw": raw, "count": len(lines), "cut": cut, "more": True,
                    "offset": offset}
        line = lines[i]
        if len(line) > READ_MAX_LINE_CHARS:
            line = line[:READ_MAX_LINE_CHARS] + READ_MAX_LINE_SUFFIX
        size = len(line.encode("utf-8")) + (1 if raw else 0)
        if used + size > READ_MAX_BYTES:
            cut = True
            break
        raw.append(line)
        used += size
    return {"raw": raw, "count": len(lines), "cut": cut, "more": cut,
            "offset": offset}


def render_read(path: str, text: str, *, offset: int = 1, limit: Optional[int] = None,
                kind: str = "file", note: Optional[str] = None) -> Dict[str, Any]:
    """文件内容 → {output 给模型, display 给人}。

    `kind` 写进 <type>：Office / PDF 的正文是抽取出来的文本，不是原始字节，
    模型需要知道这一点；`note` 是一行结构提示（有哪些工作表 / 幻灯片 / 页）。
    """
    sl = slice_lines(text, offset=offset, limit=limit)
    raw: List[str] = sl["raw"]
    first = sl["offset"]
    last = first + len(raw) - 1

    body = "\n".join(f"{first + i}: {ln}" for i, ln in enumerate(raw))
    if sl["cut"]:
        tail = (f"(Output capped at {READ_MAX_BYTES_LABEL}. Showing lines "
                f"{first}-{last}. Use offset={last + 1} to continue.)")
    elif sl["more"]:
        tail = (f"(Showing lines {first}-{last} of {sl['count']}. "
                f"Use offset={last + 1} to continue.)")
    else:
        tail = f"(End of file - total {sl['count']} lines)"

    parts_line = f"<parts>{note}</parts>\n" if note else ""
    output = (f"<path>{path}</path>\n<type>{kind}</type>\n{parts_line}<content>\n"
              f"{body}\n\n{tail}\n</content>")

    shown, clipped = _clip_for_display(raw)
    return {
        "output": output,
        "display": {
            "type": "file",
            "path": path,
            "kind": kind,
            "text": "\n".join(shown),
            "lineStart": first,
            "lineEnd": first + len(shown) - 1,
            "totalLines": sl["count"],
            "truncated": sl["cut"] or sl["more"],
            "clipped": clipped,
            "nextOffset": last + 1 if (sl["cut"] or sl["more"]) else 0,
        },
    }


def render_dir(path: str, entries: Sequence[str], *, offset: int = 1,
               limit: Optional[int] = None) -> Dict[str, Any]:
    """目录内容 → {output, display}。目录也走 read（OpenCode 就是这样合并的）。"""
    items = sorted(str(e) for e in entries)
    offset = max(1, int(offset or 1))
    limit = int(limit) if limit else READ_DEFAULT_LINES
    start = offset - 1
    sliced = items[start:start + limit]
    truncated = start + len(sliced) < len(items)

    if truncated:
        note = (f"\n(Showing {len(sliced)} of {len(items)} entries. Use 'offset' "
                f"parameter to read beyond entry {offset + len(sliced)})")
    else:
        note = f"\n({len(items)} entries)"
    output = (f"<path>{path}</path>\n<type>directory</type>\n<entries>\n"
              f"{chr(10).join(sliced)}{note}\n</entries>")
    return {
        "output": output,
        "display": {
            "type": "directory",
            "path": path,
            "entries": sliced,
            "offset": offset,
            "totalEntries": len(items),
            "truncated": truncated,
        },
    }


# ── 写入 ──────────────────────────────────────────────────────────────
def unified_diff(path: str, old: str, new: str) -> str:
    """unified diff（stdlib difflib；OpenCode 用的是 `diff` 包，同一家族）。"""
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}", n=3,
    ))


def render_write(path: str, old: str, new: str, *, existed: bool) -> Dict[str, Any]:
    """写入回执 → {output, display}。

    回执带 diff 是关键：模型写完需要知道「到底改了什么」才能接着做事，
    只回一句 "ok" 它无从校验；人也要靠 diff 判断这次写入是否正确。
    """
    diff = unified_diff(path, old, new)
    diff_lines = diff.splitlines()
    clipped = len(diff_lines) > DIFF_MAX_LINES
    shown_diff = diff_lines[:DIFF_MAX_LINES]
    added = sum(1 for l in shown_diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in shown_diff if l.startswith("-") and not l.startswith("---"))
    verb = "Updated file" if existed else "Created file"
    head = f"{verb} successfully: {path}"

    if clipped:
        output = (f"{head}\n\n{diff}\n\n"
                  f"(diff 只显示前 {DIFF_MAX_LINES} 行，共 {len(diff_lines)} 行)")
    else:
        output = f"{head}\n\n{diff}" if diff else f"{head}（内容无变化）"

    return {
        "output": output,
        "display": {
            "type": "diff",
            "path": path,
            "existed": existed,
            "added": added,
            "removed": removed,
            "truncated": clipped,
            "diff": "\n".join(shown_diff),
            # html/svg 附带新内容，前端可直接渲染预览
            "preview": new[:PREVIEW_MAX_CHARS] if PREVIEW_RE.search(path) else "",
            "previewComplete": len(new) <= PREVIEW_MAX_CHARS,
        },
    }


# ── 编辑（精确替换）──────────────────────────────────────────────────
def apply_edit(text: str, old_string: str, new_string: str, *,
               replace_all: bool = False) -> Tuple[str, Dict[str, Any]]:
    """精确字符串替换（OpenCode edit 工具的核心语义）。

    三条硬规则，每条的报错都告诉模型**下一步该做什么**：

    1. old_string 必须**唯一**。多处命中时默认报错而不是随便改第一处——
       改错地方比改不了更糟。`replace_all=True` 才放行全改。
    2. 命中片段明显比 old_string 大得多 → 拒绝。说明模型给的是一小段、
       我们的匹配逻辑却糊上了一大片（缩进/空白差异被宽松处理了）。
    3. 找不到 → 明确告诉它必须逐字符一致（含空白、缩进、换行）。

    """
    if not old_string:
        raise FileOpError(
            "old_string 不能为空。若要整体替换整个文件，请用 write。")

    idx = text.find(old_string)
    if idx == -1:
        raise FileOpError(
            "在文件中找不到 old_string。它必须逐字符一致，"
            "包括空白、缩进与行尾（提示：若内容来自 read 输出，"
            "行号前缀「N: 」不属于文件内容，不要复制进去）。")

    last = text.rfind(old_string)
    if idx != last and not replace_all:
        raise FileOpError(
            "找到多处相同的 old_string，无法确定改哪一处。"
            "请提供更多上下文使其唯一，或改用 replace_all。")

    if _is_disproportionate(old_string, text[idx:idx + len(old_string)]):
        raise FileOpError(
            "拒绝替换：实际命中的片段远大于 old_string。"
            "请重新读取该文件，并给出完整准确的 old_string。")

    if replace_all:
        n = text.count(old_string)
        return text.replace(old_string, new_string), {"replaced": n, "mode": "all"}
    return text[:idx] + new_string + text[idx + len(old_string):], {"replaced": 1, "mode": "one"}


def _is_disproportionate(old_string: str, matched: str) -> bool:
    old_lines = old_string.count("\n") + 1
    got_lines = matched.count("\n") + 1
    if got_lines >= max(old_lines + 3, old_lines * 2):
        return True
    # 单行 old_string 不按长度比（短字符串换行差异不该被拒）
    return False


def render_edit(path: str, old: str, new: str, info: Dict[str, Any]) -> Dict[str, Any]:
    """编辑回执 → {output, display}（回执带 diff，与 write 一致）。"""
    r = render_write(path, old, new, existed=True)
    n = info.get("replaced") or 0
    mode = "全部" if info.get("mode") == "all" else "1 处"
    head = f"Edited file successfully: {path}（替换 {mode}，共 {n} 处）"
    return {"output": f"{head}\n\n{r['output']}", "display": r["display"]}


def _clip_for_display(lines: Sequence[str]) -> Tuple[List[str], bool]:
    """display 文本封顶（随 trace 落库，不能无限长）。"""
    out: List[str] = []
    used = 0
    for ln in lines:
        if len(out) >= DISPLAY_MAX_LINES or used + len(ln) > DISPLAY_MAX_CHARS:
            return out, True
        out.append(ln)
        used += len(ln) + 1
    return out, len(out) < len(lines)


# ── 语法校验（代码稳定性）───────────────────────────────────────────
# 写坏语法是 vibecoding 工具最常见的失败模式：模型写一个有语法错误的文件，
# 下一步 run 报 traceback，模型盲改 → 循环烧 token。写完立即校验、失败回滚，
# 把错误在**当轮**反馈给模型，比事后调试便宜一个数量级。
def validate_syntax(path: str, content: str) -> Optional[str]:
    """返回 None 表示通过；否则返回可执行的错误描述（含行号）。

    只校验**能零依赖校验**的类型：
      .py    → 内置 compile()（无需 subprocess）
      .json  → json.loads（注意：空文件合法 JSON 是失败，但空文件常见且无害，
               所以空内容放行）
      .js/.mjs/.cjs → `node --check`（node 存在才校验；缺失则跳过）
    其余类型返回 None（不臆测）。
    """
    ext = os.path.splitext(str(path or ""))[1].lower()
    if ext == ".py":
        try:
            compile(content, str(path), "exec")
        except SyntaxError as e:
            line = e.lineno or "?"
            return f"Python 语法错误（第 {line} 行）: {e.msg}"
        except ValueError as e:  # 空字节 / null 字符等
            return f"Python 文件非法: {e}"
        return None
    if ext == ".json":
        if not content.strip():
            return None
        try:
            json.loads(content)
        except Exception as e:
            return f"JSON 解析失败: {e}"
        return None
    if ext in (".js", ".mjs", ".cjs"):
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            return None  # 环境没有 node，不臆测
        try:
            p = subprocess.run([node, "--check", "-"],
                               input=content, capture_output=True, text=True,
                               timeout=10)
        except Exception:
            return None
        if p.returncode != 0:
            msg = (p.stderr or "").strip()
            return f"JS 语法错误: {msg[:400]}"
        return None
    return None


# ── 信封拆解（对话与任务共用）───────────────────────────────────────
def unwrap_display(result: Any) -> Any:
    """工具结果 → **给模型的正文**：有信封就剥掉壳，只留 output。

    为什么必须剥：信封里的 display 与 output 内容大量重复（同一份文件正文
    出现两次）。直接把整个信封 json.dumps 进上下文，等于把上下文翻倍——
    一次读文件多塞十几 KB，而且模型看到 JSON 壳反而不容易认出「这是文件内容」。

    非文件类结果原样返回（列表 / 标量 / 无 output 键的字典）。
    """
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            return result
    if isinstance(result, dict) and isinstance(result.get("output"), str):
        return result["output"]
    return result


def summarize_display(display: Optional[Dict[str, Any]]) -> str:
    """展示载荷 → 一句人话摘要（折叠标题那行）。

    没有这一步，文件类工具的摘要会退化成「返回字段：output, display」——
    对用户零信息量，而此时明明有路径和行号区间可写。
    """
    if not isinstance(display, dict):
        return ""
    kind = display.get("type")
    path = str(display.get("path") or "")
    if kind == "file":
        return (f"{path}（第 {display.get('lineStart')}–{display.get('lineEnd')} 行"
                f" / 共 {display.get('totalLines')} 行）")
    if kind == "diff":
        verb = "已覆盖" if display.get("existed") else "已新建"
        return f"{path}（{verb} +{display.get('added')} −{display.get('removed')}）"
    if kind == "directory":
        shown = len(display.get("entries") or [])
        total = display.get("totalEntries")
        inner = f"共 {total} 项" if shown == total else f"显示 {shown} / 共 {total} 项"
        return f"{path}（{inner}）"
    return path
