"""MCP 服务接入（本地 stdio 与远程 HTTP/SSE）。

- 服务配置存 `app_settings` 的 `mcp` 键，界面可增删改、导出导入；
- 自动安装：`npm install <包>` 落在 data/agent_zero/mcp，用 `npx -y <包>` 启动；
- 工具名 `mcp__<server>__<tool>` 注入到模型可见的工具列表，
  危险等级由权限系统按名字判定（exec/write/delete 类高危）。
"""

from __future__ import annotations

import asyncio
import os
import json
import logging
import re
import shlex
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.agent_zero.database import get_db_path
from services.agent_zero.stores import settings_store, zero_store

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
CALL_TIMEOUT_S = 60.0
LIST_TIMEOUT_S = 30.0
TOOL_CACHE_TTL_S = 60.0


def _init_params() -> Dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "clientInfo": {"name": "agent-zero", "version": "1.0"},
    }


def mcp_root() -> Path:
    root = Path(os.path.dirname(get_db_path())) / "mcp"
    root.mkdir(parents=True, exist_ok=True)
    return root


# ── 配置 ────────────────────────────────────────────────────────────────

def list_servers() -> List[Dict[str, Any]]:
    raw = settings_store.get("mcp", {}) or {}
    return list(raw.get("servers") or [])


def get_server(server_id: str) -> Optional[Dict[str, Any]]:
    return next((s for s in list_servers() if s.get("id") == server_id), None)


def cached_tools(server_id: str) -> List[Dict[str, Any]]:
    """最近一次列出的工具（不启动进程；用于状态页展示）。"""
    return list((_tool_cache.get(server_id) or {}).get("tools") or [])


def _save(servers: List[Dict[str, Any]]) -> None:
    settings_store.put("mcp", {"servers": servers})


def _normalize(cfg: Dict[str, Any]) -> Dict[str, Any]:
    name = str(cfg.get("name") or "").strip() or str(cfg.get("package") or cfg.get("url") or "MCP")
    stype = str(cfg.get("type") or "stdio").strip().lower()
    if stype not in ("stdio", "http"):
        raise ValueError("MCP 类型必须是 stdio 或 http")
    out: Dict[str, Any] = {
        "id": str(cfg.get("id") or zero_store.new_id("mcp")),
        "name": name[:120],
        "type": stype,
        "enabled": bool(cfg.get("enabled", True)),
        "package": str(cfg.get("package") or "").strip()[:200],
        "command": str(cfg.get("command") or "").strip()[:500],
        "args": [str(a) for a in (cfg.get("args") or [])][:40],
        "install_command": str(cfg.get("install_command") or "").strip()[:500],
        "env": {str(k): str(v) for k, v in (cfg.get("env") or {}).items() if str(k)} if isinstance(cfg.get("env"), dict) else {},
        "url": str(cfg.get("url") or "").strip()[:500],
        "headers": {str(k): str(v) for k, v in (cfg.get("headers") or {}).items() if str(k)} if isinstance(cfg.get("headers"), dict) else {},
        "note": str(cfg.get("note") or "").strip()[:500],
        "installed": bool(cfg.get("installed")),
        # persistent：进程常驻。有页面 / 会话状态的服务必须常驻，
        # 否则每次工具调用都重启，上一步的页面状态会丢。
        "persistent": bool(cfg.get("persistent")),
        # tool_allowlist：只把列出的工具注入模型；空 = 不限制。
        "tool_allowlist": [str(t).strip()[:120] for t in (cfg.get("tool_allowlist") or [])
                           if str(t).strip()][:200],
        "created_at": str(cfg.get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S")),
    }
    # 允许把整条启动命令直接粘进 command（例如 "/opt/homebrew/bin/node /x/server.js"）。
    if out["command"] and not out["args"] and " " in out["command"]:
        try:
            parts = shlex.split(out["command"])
            out["command"], out["args"] = parts[0], parts[1:]
        except ValueError:
            pass
    if stype == "stdio" and not out["command"]:
        if out["package"]:
            out["command"] = "npx"
            out["args"] = ["-y", out["package"]] + out["args"]
        else:
            raise ValueError("stdio MCP 需要 command（或 package）")
    if stype == "http" and not out["url"]:
        raise ValueError("http MCP 需要 url")
    return out


def add_server(cfg: Dict[str, Any]) -> Dict[str, Any]:
    server = _normalize(cfg)
    servers = list_servers()
    if any(s.get("name") == server["name"] for s in servers):
        raise ValueError(f"已存在同名 MCP 服务: {server['name']}")
    servers.append(server)
    _save(servers)
    zero_store.append_audit("local", "mcp_add", args={"server": server["id"], "name": server["name"]})
    return server


def update_server(server_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    servers = list_servers()
    for i, s in enumerate(servers):
        if s.get("id") != server_id:
            continue
        merged = dict(s)
        for key in ("name", "type", "command", "args", "env", "url", "headers",
                    "package", "install_command", "enabled", "note", "installed",
                    "persistent", "tool_allowlist"):
            if key in patch:
                merged[key] = patch[key]
        normalized = _normalize(merged)
        servers[i] = normalized
        _save(servers)
        # 只有启动配置 / 开关真正变了才重启进程：改个备注不该丢掉常驻进程里的页面状态。
        launch_keys = ("type", "command", "args", "env", "url", "persistent",
                       "enabled")
        if any(s.get(key) != normalized.get(key) for key in launch_keys):
            _discard_client(server_id)
        return servers[i]
    raise ValueError(f"MCP 服务不存在: {server_id}")


def delete_server(server_id: str) -> bool:
    servers = list_servers()
    kept = [s for s in servers if s.get("id") != server_id]
    if len(kept) == len(servers):
        return False
    _save(kept)
    _discard_client(server_id)
    return True


def export_servers() -> Dict[str, Any]:
    return {"version": 1, "servers": list_servers()}


def import_servers(payload: Dict[str, Any]) -> Dict[str, Any]:
    servers = payload.get("servers") if isinstance(payload, dict) else payload
    if not isinstance(servers, list):
        raise ValueError("导入内容必须是 {\"servers\": [...]}")
    added = 0
    for cfg in servers:
        try:
            add_server(cfg)
            added += 1
        except Exception as e:
            logger.debug("跳过 MCP 条目: %s", e)
    return {"added": added, "total": len(list_servers())}


# ── 自动安装（npm） ─────────────────────────────────────────────────────

def npm_available() -> bool:
    return shutil.which("npm") is not None


async def install_package(package: str, *, command: str = "") -> Dict[str, Any]:
    """安装 MCP 依赖。

    - command 为空：走内置 `npm install <package>`；
    - command 非空：用户手动指定完整命令（建议绝对路径，例如
      `/opt/homebrew/bin/npm install ...` 或 `brew install node`），在 MCP 根目录
      用登录 shell 执行。这样 .app 不继承终端 PATH / 本机没有 npm 时也能手动接管。
    """
    pkg = str(package or "").strip()
    custom = str(command or "").strip()
    if not pkg and not custom:
        return {"ok": False, "error": "缺少包名或安装命令"}
    if custom:
        root = mcp_root()
        try:
            proc = await asyncio.create_subprocess_shell(
                custom,
                cwd=str(root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                executable="/bin/zsh",
                env=_install_env(),
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return {"ok": False, "error": "安装命令超时（300s）"}
        except Exception as e:
            return {"ok": False, "error": f"安装命令失败: {e}"}
        ok = proc.returncode == 0
        return {
            "ok": ok,
            "package": pkg,
            "command": custom,
            "output": (out or b"").decode("utf-8", "replace")[-2000:],
            "error": (err or b"").decode("utf-8", "replace")[-2000:] if not ok else "",
        }
    if not npm_available():
        return {
            "ok": False,
            "error": "本机未找到 npm。可在「安装命令」里手动填写完整命令（绝对路径），"
                     "或改用 command + args 手动启动 MCP 服务。",
        }
    if not _valid_package(pkg):
        return {"ok": False, "error": f"非法包名: {pkg}"}
    root = mcp_root()
    if not (root / "package.json").exists():
        (root / "package.json").write_text(
            json.dumps({"name": "agent-zero-mcp", "version": "1.0.0", "private": True},
                       ensure_ascii=False), encoding="utf-8")
    try:
        proc = await asyncio.create_subprocess_exec(
            "npm", "install", "--no-audit", "--no-fund", pkg,
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=_install_env(),
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {"ok": False, "error": "npm install 超时（180s）"}
    except Exception as e:
        return {"ok": False, "error": f"npm install 失败: {e}"}
    ok = proc.returncode == 0
    return {
        "ok": ok,
        "package": pkg,
        "output": (out or b"").decode("utf-8", "replace")[-1500:],
        "error": (err or b"").decode("utf-8", "replace")[-1500:] if not ok else "",
    }


def _install_env() -> Dict[str, str]:
    """安装命令的环境：补上 Homebrew / nvm 常见目录，GUI 启动也能找到 npm。"""
    env = dict(os.environ)
    extra = [
        "/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin",
        str(Path.home() / ".local" / "bin"),
        str(Path.home() / ".bun" / "bin"),
    ]
    for base in ("~/.nvm/versions/node",):
        try:
            node_root = Path(base).expanduser()
            if node_root.is_dir():
                for child in sorted(node_root.iterdir(), reverse=True):
                    extra.append(str(child / "bin"))
        except Exception:
            pass
    parts = [p for p in env.get("PATH", "").split(":") if p]
    for p in extra:
        if p and p not in parts:
            parts.append(p)
    env["PATH"] = ":".join(parts)
    return env


def _valid_package(pkg: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9_./@\-]{1,120}$", pkg))


def mark_installed(server_id: str, installed: bool) -> Dict[str, Any]:
    return update_server(server_id, {"installed": installed})


# ── 客户端 ──────────────────────────────────────────────────────────────

_clients: Dict[str, Any] = {}
_tool_cache: Dict[str, Dict[str, Any]] = {}
# server_id -> 最近一次使用时间（persistent 进程的空闲回收依据）
_last_used: Dict[str, float] = {}
# 同步线程里被弃用、但当时没有事件循环可 await 的进程；交给 shutdown 统一停。
_orphan_clients: List[Any] = []
PERSISTENT_IDLE_S = 900.0


def _discard_client(server_id: str) -> None:
    """丢弃 client 并（在有事件循环时）异步停掉子进程。

    同步路由跑在线程池里没有事件循环：这时进程句柄仍被 asyncio 持有，
    只能等进程退出；因此会真正用到常驻进程的路由（MCP 更新 / 删除）
    都改成 async，保证 stop() 能被调度。
    """
    client = _clients.pop(server_id, None)
    _tool_cache.pop(server_id, None)
    _last_used.pop(server_id, None)
    if client is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 线程池里没有 loop：先记下来，应用退出 / 下次 stop_client 时再停。
        _orphan_clients.append((server_id, client))
        return
    loop.create_task(client.stop())


async def stop_client(server_id: str) -> None:
    """立即停止某个 MCP 服务的常驻进程（切换配置 / 停用时调用）。"""
    client = _clients.pop(server_id, None)
    _tool_cache.pop(server_id, None)
    _last_used.pop(server_id, None)
    orphans = [item for item in _orphan_clients if item[0] == server_id]
    _orphan_clients[:] = [item for item in _orphan_clients if item[0] != server_id]
    if client is not None:
        await client.stop()
    for _, orphan in orphans:
        try:
            await orphan.stop()
        except Exception:
            logger.debug("停止遗留 MCP 进程失败", exc_info=True)


async def reap_idle(max_idle_s: float = PERSISTENT_IDLE_S,
                    exclude: str = "") -> List[str]:
    """回收空闲太久的常驻 MCP 进程（不常使用，不能一直占内存）。"""
    now = time.time()
    victims = [sid for sid, ts in list(_last_used.items())
               if sid != exclude and now - ts >= max_idle_s]
    for sid in victims:
        await stop_client(sid)
    return victims


async def shutdown_all() -> None:
    """应用退出时停掉所有常驻 MCP 进程（含线程里弃用的遗留进程）。"""
    for sid in list(_clients.keys()):
        await stop_client(sid)
    orphans = list(_orphan_clients)
    _orphan_clients.clear()
    for _, orphan in orphans:
        try:
            await orphan.stop()
        except Exception:
            logger.debug("停止遗留 MCP 进程失败", exc_info=True)


class McpError(Exception):
    pass


class StdioClient:
    """极简 MCP stdio 客户端：换行分隔的 JSON-RPC。"""

    def __init__(self, server: Dict[str, Any]):
        self.server = server
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._id = 0
        self._lock = asyncio.Lock()
        # stderr 必须持续读走：MCP 服务（尤其 Node）往 stderr 打日志时，
        # 管道写满（~64KB）子进程就会卡死，之后所有工具调用一起超时。
        self._stderr_task: Optional[asyncio.Task] = None
        self._stderr_tail = b""

    async def _send(self, method: str, params: Dict[str, Any], notify: bool = False) -> Any:
        if self.proc is None or self.proc.stdin is None:
            raise McpError("MCP 进程未启动")
        self._id += 1
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            msg["id"] = self._id
        line = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        self.proc.stdin.write(line)
        await self.proc.stdin.drain()
        if notify:
            return None
        while True:
            if self.proc.stdout is None:
                raise McpError("MCP 进程没有标准输出")
            raw = await self.proc.stdout.readline()
            if not raw:
                raise McpError("MCP 进程结束（无响应）")
            try:
                parsed = json.loads(raw.decode("utf-8", "replace").strip())
            except Exception:
                continue
            if parsed.get("id") == msg.get("id"):
                if parsed.get("error"):
                    raise McpError(str(parsed["error"].get("message") or parsed["error"]))
                return parsed.get("result")

    async def start(self) -> None:
        if self.proc is not None:
            return
        command = self.server.get("command") or ""
        argv = shlex.split(command) if command else []
        args = list(self.server.get("args") or [])
        if not argv:
            raise McpError("缺少启动命令")
        env = dict(self.server.get("env") or {})
        # 只透传运行必需的基础变量：常驻服务要 HOME/TMPDIR 放配置与临时文件，
        # 代理与语言环境也常被上游用到；其余宿主变量不泄漏给子进程。
        base_keys = ("PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL",
                     "TZ", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
                     "http_proxy", "https_proxy", "no_proxy")
        proc_env = {key: os.environ[key] for key in base_keys if key in os.environ}
        proc_env.update(env)
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *argv, *args,
                cwd=str(mcp_root()),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=proc_env,
            )
        except Exception as e:
            raise McpError(f"启动失败: {e}")
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        try:
            await asyncio.wait_for(
                self._send("initialize", {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "agent-zero", "version": "1.0"},
                }),
                timeout=LIST_TIMEOUT_S,
            )
            await self._send("notifications/initialized", None, notify=True)
        except BaseException:
            # 半启动（进程在、握手失败）不能留着：下次调用会以为已就绪
            await self.stop()
            raise

    async def _drain_stderr(self) -> None:
        """把 stderr 读进一个有界尾巴：防止管道写满卡死，同时留最近日志排错。"""
        proc = self.proc
        stream = getattr(proc, "stderr", None)
        if stream is None:
            return
        try:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    return
                self._stderr_tail = (self._stderr_tail + chunk)[-8192:]
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def stop(self) -> None:
        proc, self.proc = self.proc, None
        task, self._stderr_task = self._stderr_task, None
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except Exception:
                    pass
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    async def _request(self, method: str, params: Dict[str, Any],
                       timeout: float) -> Any:
        """一次请求：失败（含超时/取消）就停掉进程，下次调用重新握手。"""
        async with self._lock:
            await self.start()
            try:
                return await asyncio.wait_for(self._send(method, params),
                                              timeout=timeout)
            except BaseException:
                await self.stop()
                raise

    async def list_tools(self) -> List[Dict[str, Any]]:
        result = await self._request("tools/list", {}, LIST_TIMEOUT_S)
        return list((result or {}).get("tools") or [])

    async def call(self, tool: str, args: Dict[str, Any]) -> Any:
        return await self._request("tools/call", {"name": tool, "arguments": args},
                                   CALL_TIMEOUT_S)


class HttpClient:
    """MCP streamable-HTTP / SSE 客户端。"""

    def __init__(self, server: Dict[str, Any]):
        self.server = server
        self.session_id: Optional[str] = None
        self._lock = asyncio.Lock()

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json",
                  "Accept": "application/json, text/event-stream"}
        headers.update(self.server.get("headers") or {})
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    async def _post(self, method: str, params: Dict[str, Any]) -> Any:
        import httpx

        body = {"jsonrpc": "2.0", "method": method, "params": params}
        if method != "initialize":
            body["id"] = 1
        async with httpx.AsyncClient(timeout=LIST_TIMEOUT_S if method == "initialize" else CALL_TIMEOUT_S) as client:
            resp = await client.post(self.server["url"], json=body, headers=self._headers())
            if resp.status_code >= 400:
                raise McpError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            sid = resp.headers.get("mcp-session-id")
            if sid:
                self.session_id = sid
            text = resp.text
        payload: Any = None
        ctype = "application/json"
        if text.strip().startswith("{") or text.strip().startswith("["):
            try:
                payload = json.loads(text)
            except Exception:
                payload = None
        else:
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    try:
                        payload = json.loads(line[5:].strip())
                        break
                    except Exception:
                        continue
        if isinstance(payload, dict) and payload.get("error"):
            raise McpError(str(payload["error"].get("message") or payload["error"]))
        _ = ctype
        return (payload or {}).get("result") if isinstance(payload, dict) else payload

    async def list_tools(self) -> List[Dict[str, Any]]:
        async with self._lock:
            await self._post("initialize", _init_params())
            result = await self._post("tools/list", {})
        return list((result or {}).get("tools") or [])

    async def call(self, tool: str, args: Dict[str, Any]) -> Any:
        async with self._lock:
            await self._post("initialize", _init_params())
            return await self._post("tools/call", {"name": tool, "arguments": args})

    async def stop(self) -> None:
        return None


def _client(server: Dict[str, Any]):
    cached = _clients.get(server["id"])
    if cached:
        return cached
    client = StdioClient(server) if server.get("type") == "stdio" else HttpClient(server)
    _clients[server["id"]] = client
    # 进程创建即记一次时间：只列过工具、从没调用过的常驻进程也会被空闲回收。
    _last_used[server["id"]] = time.time()
    return client


async def server_tools(server_id: str, refresh: bool = False) -> List[Dict[str, Any]]:
    server = get_server(server_id)
    if not server:
        raise McpError(f"MCP 服务不存在: {server_id}")
    cached = _tool_cache.get(server_id)
    if cached and not refresh and time.time() - cached["ts"] < TOOL_CACHE_TTL_S:
        return cached["tools"]
    await reap_idle(exclude=server_id)
    _last_used[server_id] = time.time()
    client = _client(server)
    try:
        tools = await client.list_tools()
    finally:
        # 常驻服务不能在这里停：进程里存着页面 / 会话状态。
        if isinstance(client, StdioClient) and not server.get("persistent"):
            await client.stop()
            _clients.pop(server_id, None)
    _tool_cache[server_id] = {"ts": time.time(), "tools": tools}
    return tools


async def enabled_tool_specs() -> List[Dict[str, Any]]:
    """所有启用的 MCP 服务工具，转成 OpenAI function spec（带命名前缀）。"""
    specs: List[Dict[str, Any]] = []
    for server in list_servers():
        if not server.get("enabled"):
            continue
        try:
            tools = await server_tools(server["id"])
        except Exception as e:
            logger.warning("MCP 工具列表失败 %s: %s", server.get("name"), e)
            continue
        allowed = set(server.get("tool_allowlist") or [])
        if allowed:
            tools = [t for t in tools if str(t.get("name") or "") in allowed]
        for tool in tools:
            name = str(tool.get("name") or "").strip()
            if not name:
                continue
            specs.append({
                "type": "function",
                "function": {
                    "name": f"mcp__{server['id']}__{name}",
                    "description": (tool.get("description") or f"{server['name']} · {name}")[:1200],
                    "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
                },
            })
    return specs


async def call_tool(server_id: str, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    server = get_server(server_id)
    if not server or not server.get("enabled"):
        return {"error": f"MCP 服务不可用: {server_id}"}
    await reap_idle(exclude=server_id)
    _last_used[server_id] = time.time()
    client = _client(server)
    try:
        result = await client.call(tool, args or {})
    except Exception as e:
        if isinstance(client, StdioClient):
            _clients.pop(server_id, None)
        return {"error": f"MCP 调用失败: {e}"}
    finally:
        if isinstance(client, StdioClient) and not server.get("persistent"):
            await client.stop()
            _clients.pop(server_id, None)
    _last_used[server_id] = time.time()
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            texts = [c.get("text", "") for c in content if isinstance(c, dict)]
            return {"ok": not result.get("isError"), "text": "\n".join(texts),
                    "structured": result.get("structuredContent")}
        return {"ok": True, "result": result}
    return {"ok": True, "result": result}


async def probe(server_id: str) -> Dict[str, Any]:
    """连接测试：能列出多少工具。"""
    try:
        tools = await server_tools(server_id, refresh=True)
        return {"ok": True, "tools": len(tools),
                "names": [t.get("name") for t in tools][:50]}
    except Exception as e:
        return {"ok": False, "error": str(e)}
