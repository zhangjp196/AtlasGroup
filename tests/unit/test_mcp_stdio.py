"""MCP stdio 稳定性：stderr 必须被持续读走，失败后必须重置进程。

典型故障：MCP 服务（尤其 Node）启动时往 stderr 打几十 KB 日志，管道写满后
子进程卡在 write 上，紧接着所有工具调用超时——看起来像"模型卡死"。
"""
from __future__ import annotations

import asyncio
import sys

from services.agent_zero.services import mcp_service

SERVER = r"""
import sys, json
sys.stderr.write("x" * 300000)
sys.stderr.flush()
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except Exception:
        continue
    if "id" not in msg:
        continue
    method = msg.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05",
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "t", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "description": "e",
                             "inputSchema": {"type": "object"}}]}
    else:
        result = {"content": [{"type": "text", "text": "ok"}]}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
"""


def test_stdio_survives_large_stderr_output(client, monkeypatch):
    monkeypatch.setattr(mcp_service, "LIST_TIMEOUT_S", 3.0)
    monkeypatch.setattr(mcp_service, "CALL_TIMEOUT_S", 3.0)

    server = {"id": "t", "type": "stdio", "command": sys.executable,
              "args": ["-u", "-c", SERVER]}
    mcp = mcp_service.StdioClient(server)

    async def run():
        try:
            tools = await mcp.list_tools()
            assert [t["name"] for t in tools] == ["echo"]
            result = await mcp.call("echo", {})
            assert "ok" in str(result)
            # stderr 被读进了有界尾巴（没有让子进程卡住）
            assert mcp._stderr_tail
        finally:
            await mcp.stop()
        assert mcp.proc is None

    asyncio.run(run())


def test_stdio_resets_process_after_timeout(client, monkeypatch):
    """超时后进程要停掉：否则 stdout 错位，下一次调用会读到上一条响应。"""
    monkeypatch.setattr(mcp_service, "LIST_TIMEOUT_S", 0.2)

    slow = {"id": "t2", "type": "stdio", "command": sys.executable,
            "args": ["-u", "-c", "import sys,time; time.sleep(5)"]}
    mcp = mcp_service.StdioClient(slow)

    async def run():
        try:
            await mcp.list_tools()
            raise AssertionError("应当超时")
        except Exception:
            pass
        assert mcp.proc is None, "超时后必须重置进程"

    asyncio.run(run())
