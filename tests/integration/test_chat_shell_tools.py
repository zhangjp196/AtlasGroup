"""纯聊天工作区（没有用户文件夹）也能用 shell / 文件工具。

约定（本测试锁定）：

- 纯聊天工作区里**只有** `workspace`（中间存储）不下发，shell / fs / dev / vcs /
  codegraph 照常可用；
- shell 的 cwd 与文件工具的相对路径根落在**托管目录**
  （``data/agent_zero/workspace/<ws_id>``），不碰用户的项目文件；
- `workspace` 工具在纯聊天工作区被拦下，历史消息里的旧调用不会写进托管目录。
"""
from __future__ import annotations

import asyncio

from services.agent_zero.runtime.zero.dispatch import ToolDispatcher
from services.agent_zero.services import workspace_service
from services.agent_zero.task_layer import conversation
from services.agent_zero.tools import registry


def _default_workspace() -> str:
    from services.agent_zero.stores import zero_store

    return zero_store.DEFAULT_WORKSPACE_ID


def test_chat_workspace_tool_specs_keep_shell_and_files(client, app_env):
    ws_id = _default_workspace()
    assert not workspace_service.has_working_dir(ws_id)

    specs = conversation._chat_tool_specs(
        {"tools": list(registry.known_codes())}, None, False, ws_id)
    names = {s["function"]["name"] for s in specs}
    assert {"shell", "fs", "net", "memory", "plan", "dev", "ask_user"} <= names
    assert "workspace" not in names
    # fs 包里是可用的文件工具（没有被"需要工作目录"整体过滤掉）
    fs = next(s for s in specs if s["function"]["name"] == "fs")
    assert set(fs["function"]["parameters"]["properties"]["op"]["enum"]) == {
        "read", "office", "write", "edit", "list"}


def test_chat_workspace_uses_managed_dir(client, app_env, monkeypatch):
    """托管目录落在 data/<workspace_dir>/<ws_id>，并且按需创建。"""
    monkeypatch.setenv("AGENT_ZERO_WORKSPACE_DIR", str(app_env["tmp"] / "wsroot"))
    from services.agent_zero import config as config_mod

    monkeypatch.setattr(config_mod, "_settings", None, raising=False)

    ws_id = _default_workspace()
    root = workspace_service.exec_root(ws_id)
    assert root.is_dir()
    assert root == (app_env["tmp"] / "wsroot" / ws_id).resolve()
    # 它不算"用户的工作目录"
    assert not workspace_service.has_working_dir(ws_id)


def test_shell_and_fs_run_in_chat_workspace(client, app_env, monkeypatch):
    monkeypatch.setenv("AGENT_ZERO_WORKSPACE_DIR", str(app_env["tmp"] / "wsroot"))
    from services.agent_zero import config as config_mod

    monkeypatch.setattr(config_mod, "_settings", None, raising=False)

    ws_id = _default_workspace()
    task = {"id": "t1", "workspace_id": ws_id, "camp": "default", "submitter": "u"}
    envelope = {"tools": list(registry.known_codes())}
    config = {"zero": {"envelope": envelope}}
    node = {"id": "n1", "props": {"envelope": envelope}}
    loop = ToolDispatcher()

    async def run_tools():
        write = await loop._dispatch(
            task, config, node,
            {"name": "fs", "arguments": {"op": "write", "path": "note.txt",
                                         "content": "hello\n"}})
        shell = await loop._dispatch(
            task, config, node, {"name": "shell", "arguments": {"cmd": "cat note.txt"}})
        middle = await loop._dispatch(
            task, config, node,
            {"name": "workspace", "arguments": {"op": "read", "path": "note.txt"}})
        return write, shell, middle

    write, shell, middle = asyncio.run(run_tools())
    assert write.get("error") is None, write
    assert shell.get("stdout", "").strip() == "hello", shell
    # 文件真的落在托管目录里，而不是仓库根目录
    assert (workspace_service.exec_root(ws_id) / "note.txt").is_file()
    # workspace（中间存储）在纯聊天工作区不可用
    assert "workspace" in str(middle.get("error") or "")
