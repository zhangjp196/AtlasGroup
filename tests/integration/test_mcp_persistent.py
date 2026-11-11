"""常驻 MCP 进程：浏览器类服务必须跨工具调用保留同一个进程（页面状态）。

stdio MCP 默认一次调用一个进程：goto 之后 click 会落到新页面上。
`persistent=True` 的服务列表 / 调用都不重启，直到空闲回收或应用退出。
"""
from __future__ import annotations

import asyncio
import re
import sys

FAKE_SERVER = r'''
import json, os, sys

COUNTER = os.environ["MCP_COUNTER_FILE"]


def bump():
    n = 0
    try:
        with open(COUNTER, encoding="utf-8") as fh:
            n = int((fh.read() or "0").strip())
    except Exception:
        n = 0
    n += 1
    with open(COUNTER, "w", encoding="utf-8") as fh:
        fh.write(str(n))
    return n


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        continue
    if "id" not in msg:
        continue
    method = msg.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "ping", "description": "ping",
                             "inputSchema": {"type": "object", "properties": {}}}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text",
                               "text": "pid=%d calls=%d" % (os.getpid(), bump())}]}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n")
    sys.stdout.flush()
'''


def _add_server(tmp_path, *, persistent: bool, allowlist=None):
    from services.agent_zero.services import mcp_service

    script = tmp_path / "fake_mcp.py"
    if not script.exists():
        script.write_text(FAKE_SERVER, encoding="utf-8")
    counter = tmp_path / "counter.txt"
    return mcp_service.add_server({
        "name": f"fake-browser-{persistent}",
        "command": sys.executable,
        "args": [str(script)],
        "env": {"MCP_COUNTER_FILE": str(counter)},
        "persistent": persistent,
        "tool_allowlist": list(allowlist or []),
    })


def _pid(result: dict) -> str:
    match = re.search(r"pid=(\d+)", str(result.get("text") or ""))
    assert match, result
    return match.group(1)


def test_persistent_server_keeps_same_process(client, tmp_path):
    from services.agent_zero.services import mcp_service

    server = _add_server(tmp_path, persistent=True)
    assert server["persistent"] is True

    async def run():
        first = await mcp_service.call_tool(server["id"], "ping", {})
        # 列出工具不能把常驻进程停掉（否则页面状态丢失）
        tools = await mcp_service.server_tools(server["id"], refresh=True)
        second = await mcp_service.call_tool(server["id"], "ping", {})
        await mcp_service.stop_client(server["id"])
        return first, second, tools

    first, second, tools = asyncio.run(run())
    assert [t["name"] for t in tools] == ["ping"]
    assert _pid(first) == _pid(second)
    assert "calls=1" in first["text"] and "calls=2" in second["text"]

    assert mcp_service.delete_server(server["id"]) is True


def test_non_persistent_server_restarts_process(client, tmp_path):
    from services.agent_zero.services import mcp_service

    server = _add_server(tmp_path, persistent=False)

    async def run():
        first = await mcp_service.call_tool(server["id"], "ping", {})
        second = await mcp_service.call_tool(server["id"], "ping", {})
        return first, second

    first, second = asyncio.run(run())
    # 非常驻：每次调用一个新进程，pid 不同
    assert _pid(first) != _pid(second)
    assert mcp_service.delete_server(server["id"]) is True


def test_idle_reap_stops_persistent_client(client, tmp_path):
    from services.agent_zero.services import mcp_service

    server = _add_server(tmp_path, persistent=True)

    async def run():
        await mcp_service.call_tool(server["id"], "ping", {})
        stopped = await mcp_service.reap_idle(max_idle_s=0)
        return stopped

    stopped = asyncio.run(run())
    assert server["id"] in stopped
    assert server["id"] not in mcp_service._clients  # noqa: SLF001
    assert mcp_service.delete_server(server["id"]) is True


def test_tool_allowlist_filters_model_specs(client, tmp_path):
    from services.agent_zero.services import mcp_service

    async def run():
        server = _add_server(tmp_path, persistent=True, allowlist=["ping"])
        first = await mcp_service.enabled_tool_specs()
        mcp_service.update_server(server["id"], {"tool_allowlist": ["other"]})
        second = await mcp_service.enabled_tool_specs()
        await mcp_service.stop_client(server["id"])
        mcp_service.delete_server(server["id"])
        return [s["function"]["name"] for s in first], [s["function"]["name"] for s in second]

    first, second = asyncio.run(run())
    assert any(name.endswith("__ping") for name in first)
    assert not any(name.endswith("__ping") for name in second)
