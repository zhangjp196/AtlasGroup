"""文件读写的**执行层**：对话与任务共用同一份实现。

为什么要共用：此前文件工具只有对话链路一份（conversation.py），任务链路走
`workspace` 工具的裸 dict —— 于是同一个「读文件」，对话里有行号/分段/续读提示，
任务里一次最多 512 KB 灌进上下文。**两条链路行为不一致，模型在任务里就退化了**，
而且这种分叉每次改动都要改两处，迟早再漂一次。

所以执行逻辑抽到这里，所有链路都只是转发：
  对话 / 任务：runtime/zero/dispatch.py::ToolDispatcher._dispatch → file_tools.exec_tool

呈现（行号 / diff / 续读提示）在 file_ops.py（纯函数，可单测）；
本模块只负责碰文件系统与错误转译。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from services.agent_zero.runtime.zero import file_ops


def _not_found(ws_id: str, rel: str) -> Dict[str, Any]:
    """文件不存在时给「你是否想找」——模型最常见的错就是把路径写错一段。"""
    from services.agent_zero.services import workspace_service as wsvc

    parent = os.path.dirname(rel) or "."
    try:
        sibs = [i["name"] for i in wsvc.list_dir(ws_id, parent)]
    except Exception:
        sibs = []
    hint = file_ops.did_you_mean(sibs, os.path.basename(rel))
    msg = f"文件不存在: {rel}"
    if hint:
        msg += ("\n\n是否想找这些？\n"
                + "\n".join(os.path.join(parent, h) if parent != "." else h
                            for h in hint))
    else:
        msg += "（父目录下也没有相近的文件名）"
    return {"error": msg, "path": rel}


def _sample(ws_id: str, rel: str) -> bytes:
    """文件头部采样（判二进制用；已知二进制扩展名直接返回，不读盘）。"""
    from services.agent_zero.services import workspace_service as wsvc

    if os.path.splitext(rel)[1].lower() in file_ops.BINARY_EXTS:
        return b""
    try:
        target = wsvc.resolve_workspace_path(ws_id, rel)
        with target.open("rb") as fh:
            return fh.read(4096)
    except Exception:
        return b""


def _envelope(rendered: Dict[str, Any]) -> Dict[str, Any]:
    return {"output": rendered["output"], "display": rendered["display"]}


def _err(msg: str, **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"error": msg}
    out.update(extra)
    return out


def _invalidate_codemap(ws_id: str) -> None:
    """文件变化后失效代码地图缓存（惰性重建，失败不影响写操作）。"""
    try:
        from services.agent_zero.services import codemap

        codemap.invalidate(ws_id)
    except Exception:
        pass


# ── 读 ──────────────────────────────────────────────────────────────
def _read_document(ws_id: str, rel: str, args: Dict[str, Any],
                   part: Optional[str] = None) -> Dict[str, Any]:
    """Office / PDF → 纯文本（附件与文件工具共用 document_service）。

    为什么不直接 read 字节：docx/xlsx 是 zip 容器，PDF 是流，按文本解码只会
    给模型一堆乱码，模型会基于乱码编出「像模像样」的解释——比报错危险得多。
    """
    from services.agent_zero.services import document_service, workspace_service as wsvc

    target = wsvc.resolve_workspace_path(ws_id, rel)
    try:
        doc = document_service.document_parts(target)
    except document_service.DocumentError as e:
        return _err(
            f"无法读取文档 {rel}: {e}",
            path=rel,
            hint="可改用 shell 跑 python 脚本处理，或把文档导出为 txt / md / csv 后再读。",
        )

    parts = doc.get("parts") or []
    selected = document_service.select_parts(parts, part)
    text = "\n\n".join(p["text"] for p in selected).strip()
    if not text:
        return _err(f"{rel} 里没有可提取的文本", path=rel)

    note = ""
    if len(parts) > 1:
        names = ", ".join(p["name"] for p in parts)
        note = f"共 {len(parts)} 段：{names}"
        if len(selected) != len(parts):
            note += f"（本次只读：{', '.join(p['name'] for p in selected)}）"
    if doc.get("note"):
        note = f"{note}；{doc['note']}" if note else doc["note"]

    rendered = file_ops.render_read(
        doc.get("path") or rel, text,
        offset=args.get("offset"), limit=args.get("limit"),
        kind=doc.get("kind") or "document", note=note or None)
    rendered["display"]["parts"] = [{"name": p["name"], "chars": len(p["text"])}
                                    for p in parts]
    return _envelope(rendered)


def exec_read(ws_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """读文件；path 传目录则列目录（与 OpenCode 一致，两者并成一个工具）。

    Office / PDF 也走这条：文本抽取后按行号返回，模型无需额外工具。
    """
    from services.agent_zero.services import document_service, workspace_service as wsvc

    args = args or {}
    rel = str(args.get("path") or "").strip()
    if not rel:
        return _err("缺少 path")
    target = wsvc.resolve_workspace_path(ws_id, rel)
    if not target.exists():
        return _not_found(ws_id, rel)
    if target.is_dir():
        return exec_list(ws_id, args)
    if document_service.is_document(rel):
        return _read_document(ws_id, rel, args, part=args.get("part"))
    if file_ops.is_binary(rel, _sample(ws_id, rel)):
        return _err(f"Cannot read binary file: {rel}（二进制文件不做文本读取）")

    data = wsvc.read_file(ws_id, rel)
    return _envelope(file_ops.render_read(
        data.get("path") or rel, str(data.get("content") or ""),
        offset=args.get("offset"), limit=args.get("limit")))


def exec_list(ws_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """列目录 —— 产出 directory 展示载荷（与 read 传目录同一条路径）。"""
    from services.agent_zero.services import workspace_service as wsvc

    args = args or {}
    rel = str(args.get("path") or ".").strip() or "."
    target = wsvc.resolve_workspace_path(ws_id, rel)
    if not target.exists():
        return _not_found(ws_id, rel)
    if not target.is_dir():
        return _err(f"不是目录: {rel}（要读文件内容请用 read）")
    entries = [i["name"] + ("/" if i["type"] == "dir" else "")
               for i in wsvc.list_dir(ws_id, rel)]
    return _envelope(file_ops.render_dir(rel, entries,
                                         offset=args.get("offset"),
                                         limit=args.get("limit")))


def exec_read_document(ws_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """显式读 Office / PDF：可以只读某个工作表 / 某页 / 某张幻灯片。

    与 `read_file` 走同一份抽取代码，差别只在 `part` 参数和报错文案——
    模型有时需要先确认「这个 xlsx 有哪些 sheet」，显式工具让这一步更明确。
    """
    from services.agent_zero.services import document_service, workspace_service as wsvc

    args = args or {}
    rel = str(args.get("path") or "").strip()
    if not rel:
        return _err("缺少 path")
    target = wsvc.resolve_workspace_path(ws_id, rel)
    if not target.exists():
        return _not_found(ws_id, rel)
    if target.is_dir():
        return _err(f"目标是目录: {rel}（Office / PDF 工具只读单个文件）")
    if not document_service.is_document(rel):
        return _err(
            f"{rel} 不是 Office / PDF 文档（支持 "
            + ", ".join(sorted(document_service.DOCUMENT_EXT))
            + "）。普通文本 / 代码用 read_file。",
            path=rel)
    return _read_document(ws_id, rel, args, part=args.get("part"))


# ── 改 ──────────────────────────────────────────────────────────────
def exec_edit(ws_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """精确字符串替换；old_string 必须唯一，报错写明下一步怎么做。"""
    from services.agent_zero.services import document_service, workspace_service as wsvc

    args = args or {}
    rel = str(args.get("path") or "").strip()
    old_s = args.get("old_string")
    new_s = args.get("new_string")
    if not rel or old_s is None or new_s is None:
        return _err("缺少 path / old_string / new_string")
    target = wsvc.resolve_workspace_path(ws_id, rel)
    if not target.exists():
        return _not_found(ws_id, rel)
    if target.is_dir():
        return _err(f"目标是目录，无法编辑: {rel}")
    if document_service.is_document(rel):
        return _err(
            f"{rel} 是 Office / PDF 容器，不能逐字符编辑（文本是抽取出来的，"
            "写回会破坏文件）。要改内容：用 shell 跑 python 脚本生成新的 docx/xlsx，"
            "或导出为 txt / md / csv 再编辑。", path=rel)

    data = wsvc.read_file(ws_id, rel)
    old_text = str(data.get("content") or "")
    try:
        new_text, info = file_ops.apply_edit(
            old_text, str(old_s), str(new_s),
            replace_all=bool(args.get("replace_all")))
    except file_ops.FileOpError as e:
        return _err(str(e), path=rel)      # 原样给模型：文案已写明下一步

    # 语法校验：坏编辑直接拒绝，磁盘上不会留下半成品
    syntax_err = file_ops.validate_syntax(rel, new_text)
    if syntax_err:
        return _err(
            f"编辑被拒绝：{rel} 语法校验失败（{syntax_err}）。文件保持原状，"
            "请修正后重试。", path=rel)
    wsvc.write_file(ws_id, rel, new_text)
    _invalidate_codemap(ws_id)
    return _envelope(file_ops.render_edit(data.get("path") or rel,
                                          old_text, new_text, info))


# ── 写 ──────────────────────────────────────────────────────────────
def exec_write(ws_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """整体写入 / 覆盖；回执带 diff，并区分新建 / 覆盖。"""
    from services.agent_zero.services import document_service, workspace_service as wsvc

    args = args or {}
    rel = str(args.get("path") or "").strip()
    if not rel:
        return _err("缺少 path")
    if args.get("content") is None:
        return _err("缺少 content")
    if document_service.is_document(rel):
        return _err(
            f"不能把纯文本写成 {rel}：Office / PDF 是二进制容器，这样写出来的文件打不开。"
            "要产出 Office 内容，请用 shell 跑 python 脚本（openpyxl / python-docx 若已安装）"
            "或输出 md / csv / txt。", path=rel)
    content = str(args.get("content"))

    target = wsvc.resolve_workspace_path(ws_id, rel)
    existed = target.exists() and target.is_file()
    if target.exists() and target.is_dir():
        return _err(f"目标是目录，无法写入: {rel}")

    # 先校验再写：坏语法直接拒绝，磁盘上不会留下半成品文件
    syntax_err = file_ops.validate_syntax(rel, content)
    if syntax_err:
        return _err(
            f"写入被拒绝：{rel} 语法校验失败（{syntax_err}）。"
            "未写入任何内容，请修正后重试。", path=rel)

    old_text = ""
    if existed:
        try:
            old_text = str(wsvc.read_file(ws_id, rel).get("content") or "")
        except Exception:
            old_text = ""
    wsvc.write_file(ws_id, rel, content)
    _invalidate_codemap(ws_id)
    return _envelope(file_ops.render_write(rel, old_text, content, existed=existed))


# ── 通用分发（给 loop.py 的 _dispatch 用）────────────────────────────
def exec_save_analysis(ws_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """保存分析 / 调研 / 报告文档；未指定 path 时落到 .atlas/<kind>/<日期>/。"""
    from services.agent_zero.services import analysis_service

    args = args or {}
    title = str(args.get("title") or "").strip()
    content = args.get("content")
    if not title:
        return _err("缺少 title（文档标题）")
    if content is None or str(content).strip() == "":
        return _err("缺少 content（文档正文）")
    try:
        info = analysis_service.save(
            ws_id, title=title, content=str(content),
            kind=str(args.get("kind") or "analysis"),
            ext=str(args.get("ext") or "md"),
            path=str(args.get("path") or ""),
        )
    except Exception as e:
        return _err(f"保存分析文档失败: {e}")
    return info


def exec_tool(code: str, ws_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """按工具 code 执行文件类工具。

    认两种命名：对话链路的长名（read_workspace_file）与任务链路的短名（read_file）。
    两套名字指同一件事，行为必须完全一致——分叉过一次，不想再分叉。
    """
    c = (code or "").strip()
    if c in ("read_workspace_file", "read_file"):
        return exec_read(ws_id, args)
    if c in ("read_office", "read_document", "office_read", "read_pdf"):
        return exec_read_document(ws_id, args)
    if c in ("list_workspace_files", "list_files"):
        return exec_list(ws_id, args)
    if c in ("edit_workspace_file", "edit_file"):
        return exec_edit(ws_id, args)
    if c in ("write_workspace_file", "write_file"):
        return exec_write(ws_id, args)
    return _err(f"未知文件工具: {code}")


def _as_obj(result: Any) -> Any:
    """字符串结果按 JSON 解析一次（失败返回 None，交给调用方兜底）。"""
    if isinstance(result, str):
        try:
            return json.loads(result)
        except Exception:
            return None
    return result


def envelope_of(result: Any) -> Optional[Dict[str, Any]]:
    """从工具结果里取出展示载荷（无则 None）。"""
    obj = _as_obj(result)
    d = obj.get("display") if isinstance(obj, dict) else None
    return d if isinstance(d, dict) and d.get("type") else None


def brief(result: Any, size: int = 0) -> str:
    """一行摘要：文件类走 display，其余按既有规则（tasks/items/files/path+content）。"""
    disp = envelope_of(result)
    if disp:
        return file_ops.summarize_display(disp)
    obj = _as_obj(result)
    if obj is None:
        return f"{size or len(result)} 字符"
    if isinstance(obj, dict) and obj.get("error"):
        return f"失败：{str(obj['error'])[:120]}"
    if isinstance(obj, dict):
        # shell / python：摘要应该是"它到底输出了什么"，而不是字段名清单。
        # （过去 shell 工具在界面上显示「返回字段：ok, exit_code, stdout, stderr」，
        #  用户完全看不出命令干了什么。）
        if "stdout" in obj or "stderr" in obj:
            bits: List[str] = []
            code = obj.get("exit_code")
            if code not in (0, None):
                bits.append(f"exit {code}")
            out = str(obj.get("stdout") or "").strip()
            err = str(obj.get("stderr") or "").strip()
            if out:
                head = " ⏎ ".join(out.splitlines()[:3])[:180]
                bits.append(f"输出 {len(out)} 字符：{head}")
            elif err:
                bits.append(f"无 stdout，stderr {len(err)} 字符：{err[:120]}")
            else:
                bits.append("无输出")
            return " · ".join(bits)
        # 网页抓取：给状态 + 大小 + 开头一句，比"返回字段：ok, status..."有用
        if "text" in obj and ("url" in obj or "status" in obj):
            head = " ".join(str(obj.get("text") or "").split())[:120]
            return (f"{obj.get('status', '')} {str(obj.get('url') or '')[:80]} · "
                    f"{obj.get('bytes', 0)} 字节"
                    + (f" · {head}" if head else ""))
        # MCP 工具：ok/text/structured → 摘要应该给出它返回的文本开头
        if "structured" in obj and "text" in obj:
            head = " ".join(str(obj.get("text") or "").split())[:160]
            return f"MCP · {head}" if head else "MCP · 无文本返回"
        # 技能类：给出技能名与正文开头，而不是字段名
        if "skill" in obj and "content" in obj:
            body = str(obj.get("content") or "")
            head = " ".join(body.split())[:160]
            return f"技能 {obj.get('skill')}（{len(body)} 字符）：{head}"
        if isinstance(obj.get("skills"), list):
            names = [str(s.get("name") or "") for s in obj["skills"]][:8]
            return f"技能 {len(obj['skills'])} 项" + (f"：{', '.join(names)}" if names else "")
        if isinstance(obj.get("servers"), list):
            names = [str(s.get("name") or s.get("id") or "") for s in obj["servers"]][:6]
            return f"MCP 服务 {len(obj['servers'])} 个" + (f"：{', '.join(names)}" if names else "")
        for k in ("tasks", "items", "files"):
            if isinstance(obj.get(k), list):
                return f"{k} {len(obj[k])} 项"
        if "path" in obj and "content" in obj:
            return f"{obj.get('path')}（{size or len(str(obj.get('content') or ''))} 字符）"
        return f"返回字段：{', '.join(list(obj)[:4])}"
    if isinstance(obj, list):
        return f"{len(obj)} 项"
    return f"{size} 字符"
