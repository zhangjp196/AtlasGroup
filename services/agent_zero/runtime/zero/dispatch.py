"""工具分发器（对话运行使用）。

执行 shell / 文件 / 工作区 / 记忆 / 网页 / 时间 / 算术。
包络门控：`envelope.tools` 白名单。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional

from services.agent_zero.runtime.zero.gdag.store import GdagStore
from services.agent_zero.runtime.zero.sandbox.base import SandboxError
from services.agent_zero.runtime.zero.sandbox.subprocess_sandbox import SubprocessSandbox
from services.agent_zero.runtime.zero import file_tools
from services.agent_zero.services import workspace_service
from services.agent_zero.tools import registry as tool_reg

logger = logging.getLogger(__name__)

SHELL_TIMEOUT_S = 0.0            # 0 = 不限（vibe coding 长命令不中途 kill）
# 前台等待工具的预算（缺省值）：超过它仍未结束就转后台监督。
DEFAULT_FOREGROUND_BUDGET_S = 45.0
# shell 空闲兜底（缺省值）：连续这么久零输出就判定挂住并杀掉。
DEFAULT_SHELL_IDLE_TIMEOUT_S = 600.0
WEB_TIMEOUT_S = 20.0
WEB_MAX_BYTES = 512_000
WEB_MAX_CHARS = 40_000

FILE_TOOL_CODES = frozenset({"read_file", "read_office", "edit_file", "write_file",
                             "list_files"})

# 技能与 MCP 管理工具（不依赖工作区，走 skill_service / mcp_service）
SKILL_TOOL_CODES = frozenset({"list_skills", "use_skill", "create_skill",
                              "merge_skills", "install_skill"})
MCP_CONFIG_CODES = frozenset({"list_mcp", "add_mcp_server", "install_mcp"})

# 超大工具输出落盘阈值缓存（config.yaml tools.max_output_chars，2s TTL）
_SPILL_CACHE: Dict[str, Any] = {"at": 0.0, "value": 40_000}
# config.yaml tools 段缓存（2s TTL）：前台预算 / shell 空闲兜底等
_TOOLS_SECTION_CACHE: Dict[str, Any] = {"at": 0.0, "value": {}}


def _host_allowed(host: str, allow: List[str]) -> bool:
    """主机是否命中 egress 允许清单（支持精确 / *.domain / .domain / CIDR）。"""
    import ipaddress

    h = str(host or "").strip().lower().rstrip(".")
    for raw in allow:
        p = str(raw or "").strip().lower()
        if not p:
            continue
        if "/" in p:
            try:
                if ipaddress.ip_address(h) in ipaddress.ip_network(p, strict=False):
                    return True
            except ValueError:
                pass
            continue
        if p.startswith("*."):
            base = p[2:]
            if h == base or h.endswith("." + base):
                return True
        elif p.startswith("."):
            if h.endswith(p):
                return True
        elif h == p or h.endswith("." + p):
            return True
    return False


class ToolDispatcher:
    def __init__(self, store: Optional[GdagStore] = None):
        self.store = store or GdagStore()

    @staticmethod
    def _envelope(config: Dict[str, Any]) -> Dict[str, Any]:
        return (config.get("zero") or {}).get("envelope") or {}

    def _workspace(self, task: Dict[str, Any]):
        # 执行根目录：有用户文件夹用它，纯聊天工作区回退到托管目录
        # （``data/agent_zero/workspace/<ws_id>``），shell 才有合法 cwd。
        return workspace_service.exec_root(task["workspace_id"])

    # ── 超大工具输出落盘（Claude Code 同做法）────────────────────────────
    #
    # 一次 shell / 一次全文读取就可能吐出几十万字符。直接塞进上下文，要么把这一轮
    # 顶爆、要么触发硬裁把更早的内容丢掉。这里把超大结果写进工作区文件，只把
    # 预览 + 路径交给模型：需要细节时它自己分页读，上下文不会被一条结果占满。

    @staticmethod
    def _spill_limit() -> int:
        """超过多少字符落盘（0 = 关闭）。config.yaml `tools.max_output_chars`。"""
        import os

        now = time.monotonic()
        cache = _SPILL_CACHE
        if now - float(cache.get("at") or 0) < 2.0:
            return int(cache.get("value") or 0)
        value = 40_000
        try:
            from services.shared.project_paths import get_project_root
            from services.shared.yaml_config import load_service_config

            root = str(get_project_root())
            path = os.environ.get("AI_PLATFORM_CONFIG_YAML") or os.path.join(
                root, "config", "agent_zero", "config.yaml")
            sec = (load_service_config(path) or {}).get("tools") or {}
            value = int(sec.get("max_output_chars", 40_000) or 0)
        except Exception:
            logger.debug("读取 tools.max_output_chars 失败（按默认值）", exc_info=True)
        cache.update(at=now, value=max(0, value))
        return max(0, value)

    @staticmethod
    def _tools_section() -> Dict[str, Any]:
        """config.yaml 的 tools 段（2s 缓存）。读不到时返回空 dict。"""
        import os

        now = time.monotonic()
        cache = _TOOLS_SECTION_CACHE
        if now - float(cache.get("at") or 0) < 2.0:
            return dict(cache.get("value") or {})
        value: Dict[str, Any] = {}
        try:
            from services.shared.project_paths import get_project_root
            from services.shared.yaml_config import load_service_config

            root = str(get_project_root())
            path = os.environ.get("AI_PLATFORM_CONFIG_YAML") or os.path.join(
                root, "config", "agent_zero", "config.yaml")
            value = dict((load_service_config(path) or {}).get("tools") or {})
        except Exception:
            logger.debug("读取 tools 段失败（按默认值）", exc_info=True)
        cache.update(at=now, value=value)
        return dict(value)

    @classmethod
    def shell_idle_timeout(cls) -> float:
        """shell 空闲兜底秒数（0 = 不判定）。config.yaml `tools.shell_idle_timeout_s`。

        **缺省时用内置默认值**（600s）而不是 0：桌面端读的是数据目录里的
        config.yaml 副本，老用户那份可能根本没有这个键——若缺键就退化成
        「永不判定」，挂死的命令又会把整轮 run 卡住。只有显式写 0 才关闭。
        """
        raw = cls._tools_section().get("shell_idle_timeout_s")
        if raw is None or raw == "":
            return DEFAULT_SHELL_IDLE_TIMEOUT_S
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return DEFAULT_SHELL_IDLE_TIMEOUT_S

    @classmethod
    def max_sleep_seconds(cls) -> float:
        """「长等待」的提示阈值（秒，0 = 不提示）。`tools.max_sleep_s`。

        **只用于提示，不改写命令**：旧实现会把 `sleep 300 && deploy.sh` 改成
        `sleep 120 && deploy.sh`，等于让后半段提前 180s 执行——定时脚本 / 长等待
        因此跑错时间点。现在命令原样执行，超过阈值只加一句提示，真正的长任务
        由前台预算（`foreground_budget_s`）转后台托管。
        """
        raw = cls._tools_section().get("max_sleep_s", 120)
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return 120.0

    @classmethod
    def cap_sleep(cls, cmd: str) -> tuple:
        """检测命令里的长等待，返回 (**原样命令**, 提示说明)。

        **命令绝不改写**（2026-11-02 修）：旧实现把 `sleep N` 截到 120s，
        `sleep 300 && ./deploy.sh` 会变成 `sleep 120 && ./deploy.sh`——后半段
        提前 3 分钟执行，定时脚本因此跑在错误的时间点（用户反馈「定时的脚本
        时间超过 120s」）。等待时长属于命令语义，不能动。

        真正要防的是「模型被一个长等待挡住」：这由前台预算
        （`tools.foreground_budget_s`，默认 45s）负责——超预算即转后台托管，
        模型继续干别的，需要结果时用 `tool_job` 监督。这里只负责把
        「这条命令会等很久」明确告诉模型。
        """
        limit = cls.max_sleep_seconds()
        if limit <= 0:
            return cmd, []
        mult = {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
        notes: List[str] = []

        for match in re.finditer(
                r"(?<![\w.-])sleep\s+([0-9]+(?:\.[0-9]+)?)([smhd]?)\b",
                str(cmd or "")):
            seconds = float(match.group(1)) * mult.get(
                (match.group(2) or "").lower(), 1.0)
            if seconds > limit:
                notes.append(f"命令内含 {match.group(0).strip()}"
                             f"（约 {int(seconds)}s）")

        for match in re.finditer(
                r"(?i)\b(?:Start-Sleep\s+(?:-Seconds|-s)\s+)"
                r"([0-9]+(?:\.[0-9]+)?)", str(cmd or "")):
            seconds = float(match.group(1))
            if seconds > limit:
                notes.append(f"命令内含 {match.group(0).strip()}"
                             f"（约 {int(seconds)}s）")

        notes = list(dict.fromkeys(notes))       # 去重且保序
        return cmd, notes

    @classmethod
    def foreground_budget(cls) -> float:
        """前台等待工具的预算秒数（0 = 一直等）。`tools.foreground_budget_s`。

        缺省时用内置默认值（45s）：慢工具必须能被转后台，否则模型又会被
        一个挂住的命令挡住整轮（缺键不能等于「关闭」）。
        """
        raw = cls._tools_section().get("foreground_budget_s")
        if raw is None or raw == "":
            return DEFAULT_FOREGROUND_BUDGET_S
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return DEFAULT_FOREGROUND_BUDGET_S

    def _spill_if_huge(self, result: Any, task: Dict[str, Any], canon: str) -> Any:
        """结果过大 → 落盘成文件，返回预览 + 路径。任何异常都不影响工具结果。"""
        try:
            limit = self._spill_limit()
            if limit <= 0:
                return result
            import json as _json

            if isinstance(result, str):
                payload = result
            else:
                payload = _json.dumps(result, ensure_ascii=False)
            if len(payload) <= limit:
                return result

            root = self._workspace(task)
            if not root:
                return result
            import os
            import uuid

            folder = os.path.join(str(root), ".atlas", "tool-output")
            os.makedirs(folder, exist_ok=True)
            name = f"{time.strftime('%Y%m%d-%H%M%S')}-{canon}-{uuid.uuid4().hex[:6]}.txt"
            path = os.path.join(folder, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(payload)
            rel = os.path.relpath(path, str(root)).replace(os.sep, "/")
            preview = payload[:4000]
            out: Dict[str, Any] = {
                "ok": bool(result.get("ok", True)) if isinstance(result, dict) else True,
                "truncated": True,
                "saved_to": rel,
                "chars": len(payload),
                "preview": preview,
                "note": (f"工具输出过大（{len(payload)} 字符），全文已保存到工作区 {rel}；"
                         "需要细节请用 fs 的 read（offset/limit 分段）读取该文件。"),
            }
            if isinstance(result, dict) and result.get("display"):
                out["display"] = result["display"]
            self._prune_spills(folder, keep=50)
            return out
        except Exception:
            logger.debug("工具输出落盘失败（保留原结果） canon=%s", canon, exc_info=True)
            return result

    @staticmethod
    def _prune_spills(folder: str, keep: int = 50) -> None:
        """只保留最近的 N 个落盘文件，避免长期占用工作区。"""
        import os

        try:
            files = [os.path.join(folder, n) for n in os.listdir(folder)]
            files = [p for p in files if os.path.isfile(p)]
            files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for path in files[keep:]:
                try:
                    os.remove(path)
                except OSError:
                    pass
        except Exception:
            logger.debug("清理工具输出落盘文件失败", exc_info=True)

    def _camp(self, task: Dict[str, Any], config: Dict[str, Any]) -> str:
        return ((config.get("zero") or {}).get("gdag", {}).get("camp")
                or task.get("camp") or "default")

    def _envelope_for(self, config: Dict[str, Any],
                      node: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if node is not None:
            props = node.get("props") or {}
            for key in ("envelope", "envelope_subset"):
                env = props.get(key)
                if isinstance(env, dict) and env:
                    return env
        return self._envelope(config)

    async def _dispatch(self, task: Dict[str, Any], config: Dict[str, Any],
                        node: Dict[str, Any], tc: Dict[str, Any],
                        caps: Any = None) -> Any:
        name = tc["name"]
        args = tc.get("arguments") or {}
        # 工具包：模型可能调「包 + op」，这里统一解析成具体工具（对话层也会先解析，
        # 这里是任务/子代理等其它入口的兜底）。旧工具名与别名原样通过。
        from services.agent_zero.tools import packs as tool_packs

        canon, args, pack_error = tool_packs.resolve_call(name, args)
        if pack_error:
            return {"error": pack_error}
        if canon.startswith("mcp__"):
            result = await self._act_mcp_tool(canon, args)
            result = self._spill_if_huge(result, task, canon)
            self._invalidate_codegraph(task, canon)
            return result
        if not task.get("workspace_id") and canon in tool_reg.WORKSPACE_BOUND_TOOLS:
            return {"error": "请先为会话绑定工作区"}
        tdef = tool_reg.get_tool(canon)
        if tdef is None:
            return {"error": f"未知工具: {name}"}
        if tdef.gated:
            env_tools = {tool_reg.canonical(t)
                         for t in (self._envelope_for(config, node).get("tools") or [])}
            if canon not in env_tools:
                return {"error": f"超出能力包络: {name}（envelope.tools 未包含 {canon}）"}
        try:
            result = await self._invoke(canon, task, config, node, args)
        except (SandboxError, workspace_service.WorkspacePathError) as e:
            result = {"error": f"执行异常: {e}"}
        except asyncio.CancelledError:
            # 强制停止：取消必须原样向上传播，不能被 except Exception 吞成普通结果。
            raise
        except Exception as e:
            logger.exception("工具 %s 执行异常", name)
            result = {"error": f"执行异常: {e}"}
        result = self._spill_if_huge(result, task, canon)
        self._invalidate_codegraph(task, canon)
        return result

    async def _invoke(self, canon: str, task: Dict[str, Any],
                      config: Dict[str, Any], node: Dict[str, Any],
                      args: Dict[str, Any]) -> Any:
        """实际执行一个工具。

        **同步工具一律丢线程池**（`asyncio.to_thread`）：文件读写 / 记忆检索 /
        codegraph / vcs 内部都是阻塞 IO（尤其 codegraph 首次建索引要扫全仓库），
        直接在事件循环里跑会把整个 loop 钉住——此时点「停止」，
        `task.cancel()` 投递不进去、SSE 也推不出帧，表现就是「停止没用」。
        放进线程后循环始终可调度，取消立即生效（线程自身跑完丢弃结果）。
        """
        if canon == "shell":
            return await self._act_shell(task, node, args)
        if canon in FILE_TOOL_CODES:
            return await asyncio.to_thread(
                file_tools.exec_tool, canon, task["workspace_id"], args)
        if canon == "save_analysis":
            return await asyncio.to_thread(
                file_tools.exec_save_analysis, task["workspace_id"], args)
        if canon == "ask_user":
            return await self._act_ask_user(task, args)
        if canon == "workspace":
            return await asyncio.to_thread(self._act_workspace, task, node, args)
        if canon == "vcs":
            return await asyncio.to_thread(self._act_vcs, task, args)
        if canon == "memory_write":
            return await asyncio.to_thread(
                self._act_memory_write, task, config, node, args)
        if canon == "memory_read":
            return await asyncio.to_thread(self._act_memory_read, task, config, args)
        if canon == "memory_forget":
            return await asyncio.to_thread(self._act_memory_forget, args)
        if canon == "codegraph":
            return await asyncio.to_thread(self._act_codegraph, task, args)
        if canon == "time":
            return {"now": time.strftime("%Y-%m-%d %H:%M:%S")}
        if canon == "calculate":
            return self._act_calculate(args)
        if canon == "tool_job":
            # 工具后台任务监督：list / status / result / wait / kill。
            # 慢工具会被转后台，模型据此**主动监督**而不是干等它结束。
            from services.agent_zero.task_layer import tool_jobs

            return await tool_jobs.handle(
                str(task.get("conversation_id") or ""), args)
        if canon == "web":
            return await self._act_web(task, config, node, args)
        if canon in SKILL_TOOL_CODES:
            return await self._act_skills(canon, args,
                                          role_id=str(task.get("role_id") or ""))
        if canon in MCP_CONFIG_CODES:
            return await self._act_mcp_config(canon, args)
        return {"error": f"工具 {canon} 未实现"}

    @staticmethod
    def _invalidate_codegraph(task: Dict[str, Any], canon: str) -> None:
        """工具可能改动工作区后，只标记索引过期；不强制模型使用 CodeGraph。"""
        if canon not in ("shell", "write_file", "edit_file",
                         "workspace", "vcs") and not canon.startswith("mcp__"):
            return
        try:
            from services.agent_zero.services import codemap

            ws_id = str(task.get("workspace_id") or "")
            if ws_id:
                codemap.invalidate(ws_id)
        except Exception:
            logger.debug("CodeGraph 失效标记失败（不影响工具结果）", exc_info=True)

    async def _act_ask_user(self, task: Dict[str, Any],
                            args: Dict[str, Any]) -> Any:
        """向用户提问并阻塞等待；问题通过 run 事件推给前端选择框。"""
        from services.agent_zero.services import ask_service

        run_id = ask_service.current_run_id()
        conv_id = str(task.get("conversation_id") or "")
        payload = ask_service.create(conv_id, run_id, args)
        if run_id:
            from services.agent_zero.task_layer import run_service

            try:
                run_service.append_event(run_id, "ask_user", {"ask_user": payload})
            except Exception:
                logger.debug("推送 ask_user 事件失败", exc_info=True)
        answer = await ask_service.wait(payload["id"], run_id or None)
        if run_id:
            try:
                run_service.append_event(run_id, "ask_user_done",
                                         {"ask_user_done": answer})
            except Exception:
                pass
        return answer

    async def _act_shell(self, task: Dict[str, Any], node: Dict[str, Any],
                         args: Dict[str, Any]) -> Any:
        cmd = str(args.get("cmd") or "")
        if not cmd:
            return {"error": "缺少 cmd"}
        cmd, sleep_notes = self.cap_sleep(cmd)
        sandbox = SubprocessSandbox(self._workspace(task), timeout_s=SHELL_TIMEOUT_S,
                                    idle_timeout_s=self.shell_idle_timeout())
        r = await sandbox.run_shell(cmd)
        out = {"ok": r.ok, "exit_code": r.exit_code, "stdout": r.stdout,
               "stderr": r.stderr, "timed_out": r.timed_out}
        if sleep_notes:
            limit = self.max_sleep_seconds()
            note = ("；".join(sleep_notes)
                    + f"（超过 {int(limit)}s 的等待不会被执行截断，命令按原样运行；"
                    "超过前台预算会自动转后台，用 tool_job 监督结果即可）")
            out["sleep_capped"] = sleep_notes
            out["note"] = note
            out["stderr"] = (str(out.get("stderr") or "") + f"\n[atlas] {note}").strip()
        if getattr(r, "pipe_stalled", False):
            out["pipe_stalled"] = True
        return out

    async def _act_web(self, task: Dict[str, Any], config: Dict[str, Any],
                       node: Dict[str, Any], args: Dict[str, Any]) -> Any:
        url = str(args.get("url") or "").strip()
        if not url:
            return {"error": "缺少 url"}
        env = self._envelope_for(config, node)
        net = str(env.get("network") or "deny").strip().lower()
        if net == "deny":
            return {"error": "网络访问被拒绝（envelope.network=deny）"}
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return {"error": "仅支持 http(s) URL 且需含主机名"}
        if net == "allowlist":
            allow = [str(x) for x in (env.get("egress_allowlist") or [])]
            if not _host_allowed(parsed.hostname, allow):
                return {"error": f"主机不在允许清单（egress_allowlist）: {parsed.hostname}"}
        try:
            import httpx

            async with httpx.AsyncClient(
                follow_redirects=True, timeout=WEB_TIMEOUT_S, max_redirects=5,
                headers={"User-Agent": "AgentZero/1.0 (+web)"},
            ) as client:
                async with client.stream("GET", url) as resp:
                    status = resp.status_code
                    ctype = str(resp.headers.get("content-type") or "")
                    final_url = str(resp.url)
                    chunks: List[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes():
                        chunks.append(chunk)
                        total += len(chunk)
                        if total >= WEB_MAX_BYTES:
                            break
                    body = b"".join(chunks)[:WEB_MAX_BYTES]
                    charset = resp.charset_encoding or "utf-8"
        except Exception as e:
            return {"error": f"抓取失败: {e}"}
        try:
            text = body.decode(charset, errors="replace")
        except Exception:
            text = body.decode("utf-8", errors="replace")
        return {
            "ok": status < 400, "status": status, "content_type": ctype,
            "url": final_url, "bytes": total,
            "text": text[:WEB_MAX_CHARS],
            "truncated": total >= WEB_MAX_BYTES or len(text) > WEB_MAX_CHARS,
        }

    def _act_workspace(self, task: Dict[str, Any], node: Dict[str, Any],
                       args: Dict[str, Any]) -> Any:
        op = str(args.get("op") or "")
        ws_id = task["workspace_id"]
        # 中间存储的语义是「用户选定文件夹里的文件」：纯聊天工作区没有这样的文件夹，
        # 该工具不下发；这里再兜一层，避免历史消息里的旧调用把结果写进托管目录。
        if not workspace_service.has_working_dir(ws_id):
            return {"error": "该工作区是纯聊天工作区，没有用户文件夹，`workspace` 工具不可用；"
                             "读写临时文件请用 fs（read_file / write_file）"}
        try:
            if op == "read":
                return file_tools.exec_read(ws_id, args)
            if op == "write":
                return file_tools.exec_write(ws_id, args)
            if op == "list":
                return file_tools.exec_list(ws_id, args)
            return {"error": f"非法 op: {op}（允许 read/write/list）"}
        except workspace_service.WorkspacePathError as e:
            return {"error": f"路径非法: {e}"}

    def _act_vcs(self, task: Dict[str, Any], args: Dict[str, Any]) -> Any:
        from services.agent_zero.services import vcs_service

        ws_id = task["workspace_id"]
        op = str((args or {}).get("op") or "status")
        try:
            if op == "summary":
                return vcs_service.summary(ws_id)
            if op == "status":
                return vcs_service.status(ws_id)
            if op == "log":
                return vcs_service.log(ws_id, limit=int(args.get("limit") or 20))
            if op == "diff":
                return vcs_service.diff(ws_id, path=args.get("path"), rev=args.get("rev"))
            if op == "branches":
                return vcs_service.branches(ws_id)
            if op == "switch":
                return vcs_service.switch(ws_id, str(args.get("branch") or ""),
                                         create=bool(args.get("create")))
            if op == "commit":
                paths = args.get("paths") or ([args["path"]] if args.get("path") else [])
                return vcs_service.commit(ws_id, str(args.get("message") or ""),
                                         paths=paths, all_changes=bool(args.get("all")))
            if op == "stage":
                paths = args.get("paths") or ([args["path"]] if args.get("path") else [])
                return vcs_service.stage(ws_id, paths)
            if op == "unstage":
                paths = args.get("paths") or ([args["path"]] if args.get("path") else [])
                return vcs_service.unstage(ws_id, paths)
            if op == "discard":
                paths = args.get("paths") or ([args["path"]] if args.get("path") else [])
                return vcs_service.discard(ws_id, paths)
            return {"error": f"非法 op: {op}（允许 status/log/diff/branches/switch/commit/stage/unstage/discard/summary）"}
        except vcs_service.VcsError as e:
            return {"error": str(e)}
        except workspace_service.WorkspacePathError as e:
            return {"error": f"路径非法: {e}"}

    def _act_memory_write(self, task: Dict[str, Any], config: Dict[str, Any],
                          node: Dict[str, Any], args: Dict[str, Any]) -> Any:
        from services.agent_zero.services import memory_service

        kind = str(args.get("kind") or "fact")
        if kind not in memory_service.MEMORY_KINDS:
            return {"error": f"非法 kind: {kind}"
                            f"（允许 {', '.join(memory_service.MEMORY_KINDS)}）"}
        try:
            return memory_service.remember(
                str(args.get("content") or ""),
                kind=kind,
                workspace_id=task.get("workspace_id"),
                conversation_id=task.get("conversation_id"),
                tags=args.get("tags") or [],
                entities=args.get("entities") or [],
            )
        except ValueError as e:
            return {"error": str(e)}

    def _act_memory_read(self, task: Dict[str, Any], config: Dict[str, Any],
                         args: Dict[str, Any]) -> Any:
        from services.agent_zero.services import memory_service

        query = str(args.get("query") or "").strip()
        top_k = max(1, min(int(args.get("top_k") or 5), 10))
        hits = memory_service.recall(query,
                                    workspace_id=task.get("workspace_id"),
                                    limit=top_k)
        return {"hits": hits}

    def _act_memory_forget(self, args: Dict[str, Any]) -> Any:
        from services.agent_zero.services import memory_service

        return {"ok": memory_service.forget(str((args or {}).get("node_id") or ""))}

    def _act_codegraph(self, task: Dict[str, Any],
                       args: Dict[str, Any]) -> Any:
        from services.agent_zero.services import codemap, memory_service

        ws_id = task["workspace_id"]
        op = str((args or {}).get("op") or "summary").strip().lower()
        query = str((args or {}).get("query") or "").strip()
        target = query or str((args or {}).get("path") or "").strip()
        limit = max(1, min(int((args or {}).get("limit") or 10), 30))
        if op in ("refresh", "reindex"):
            return codemap.refresh(ws_id)
        if op == "status":
            return codemap.status(ws_id)
        if op == "find":
            if not query:
                return {"error": "find 需要 query"}
            hits = codemap.find_symbol(ws_id, query, limit)
            related = memory_service.recall(query, workspace_id=ws_id, limit=3)
            return {"query": query, "hits": hits, "related_memories": related}
        if op == "deps":
            if not target:
                return {"error": "deps 需要 query 或 path"}
            return codemap.dependencies(ws_id, target)
        if op == "impact":
            if not target:
                return {"error": "impact 需要 query 或 path"}
            return codemap.impact(ws_id, target)
        return {
            "status": codemap.status(ws_id),
            "map": codemap.render_map(ws_id, max_chars=2200),
        }

    def _act_calculate(self, args: Dict[str, Any]) -> Any:
        import ast
        import operator

        expr = str(args.get("expr") or "").strip()
        if not expr:
            return {"error": "expr 为空"}

        _BIN = {ast.Add: operator.add, ast.Sub: operator.sub,
                ast.Mult: operator.mul, ast.Div: operator.truediv,
                ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
                ast.Pow: operator.pow}

        def _ev(n: ast.AST) -> float:
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
                return n.value
            if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
                return _BIN[type(n.op)](_ev(n.left), _ev(n.right))
            if isinstance(n, ast.UnaryOp):
                v = _ev(n.operand)
                if isinstance(n.op, ast.USub):
                    return -v
                if isinstance(n.op, ast.UAdd):
                    return v
            raise ValueError("不允许的表达式成分")

        try:
            return {"result": _ev(ast.parse(expr, mode="eval").body)}
        except ZeroDivisionError:
            return {"error": "除以零"}
        except Exception as e:
            return {"error": f"表达式非法: {e}"}

    async def _act_mcp_tool(self, name: str, args: Dict[str, Any]) -> Any:
        """执行 mcp__<server_id>__<tool> 形式的 MCP 工具。"""
        from services.agent_zero.services import mcp_service

        parts = name.split("__", 2)
        if len(parts) != 3 or not parts[1] or not parts[2]:
            return {"error": f"非法 MCP 工具名: {name}（应为 mcp__<server>__<tool>）"}
        return await mcp_service.call_tool(parts[1], parts[2], args or {})

    async def _act_skills(self, canon: str, args: Dict[str, Any],
                          role_id: str = "") -> Any:
        from services.agent_zero.services import skill_service

        rid = str(role_id or "").strip()
        try:
            if canon == "list_skills":
                return {"skills": skill_service.list_skills(role_id=rid or None)}
            if canon == "use_skill":
                skill = skill_service.find_skill(str(args.get("name") or ""),
                                                 role_id=rid or None)
                if not skill:
                    return {"error": f"技能不存在: {args.get('name')}",
                            "available": [s["name"] for s in
                                          skill_service.list_skills(role_id=rid or None)]}
                return {"skill": skill["name"], "description": skill["description"],
                        "content": skill_service.read_skill_text(skill),
                        "files": skill.get("files") or [],
                        "role_id": str(skill.get("role_id") or "")}
            if canon == "create_skill":
                skill = skill_service.create_skill(
                    str(args.get("name") or ""), str(args.get("description") or ""),
                    str(args.get("content") or ""), role_id=rid or None)
                return {"ok": True, "skill": skill}
            if canon == "merge_skills":
                skill = skill_service.merge_skills(
                    rid, args.get("skill_ids") or [],
                    str(args.get("name") or ""), str(args.get("description") or ""))
                return {"ok": True, "skill": skill}
            if canon == "install_skill":
                source = str(args.get("source") or args.get("repo") or "").strip()
                if source:
                    return await skill_service.install_from_skills_cli(
                        source, args.get("skills") or [], role_id=rid or None)
                return await skill_service.install_from_npm(
                    str(args.get("package") or ""), str(args.get("name") or ""),
                    role_id=rid or None)
        except Exception as e:
            return {"error": f"技能操作失败: {e}"}
        return {"error": f"技能工具 {canon} 未实现"}

    async def _act_mcp_config(self, canon: str, args: Dict[str, Any]) -> Any:
        from services.agent_zero.services import mcp_service

        try:
            if canon == "list_mcp":
                servers = []
                for s in mcp_service.list_servers():
                    tools = mcp_service._tool_cache.get(s["id"], {}).get("tools") or []
                    servers.append({"id": s["id"], "name": s["name"], "type": s["type"],
                                   "enabled": s["enabled"], "tools": len(tools),
                                   "installed": s.get("installed", False)})
                return {"servers": servers, "npm": mcp_service.npm_available()}
            if canon == "add_mcp_server":
                return {"ok": True, "server": mcp_service.add_server(args)}
            if canon == "install_mcp":
                pkg = str(args.get("package") or "")
                res = await mcp_service.install_package(
                    pkg, command=str(args.get("command") or ""))
                if not res.get("ok"):
                    return res
                server = mcp_service.add_server({
                    "name": str(args.get("name") or pkg),
                    "type": "stdio",
                    "package": pkg,
                    "args": args.get("args") or [],
                    "installed": True,
                })
                try:
                    tools = await mcp_service.server_tools(server["id"], refresh=True)
                    res["tools"] = [t.get("name") for t in tools][:50]
                except Exception as e:
                    res["tools_error"] = str(e)
                res["server"] = server
                return res
        except Exception as e:
            return {"error": f"MCP 操作失败: {e}"}
        return {"error": f"MCP 工具 {canon} 未实现"}
