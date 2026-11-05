"""已下线工具（python）的残留清理回归测试。

背景（2026-10-30 死代码清理）：独立的 `python` 工具早就下线，模型统一用
`shell` 跑 python 脚本。但它在几个地方还能被看见 / 被下发：

1. `zero_store.DEFAULT_TOOLS`（新角色 envelope 的默认工具清单）；
2. 老库里已有角色的 `envelope.tools`（历史数据，不会自己消失）；
3. 前端 `TOOL_LABELS` / `TOOL_ICONS` / `tool.python` 文案；
4. `dispatch._invalidate_codegraph` 的白名单。

留着它的后果不是报错，而是「工具清单」里永远躺着一个 registry 不存在的 code，
模型偶尔会去调一个不存在的工具。

这组测试钉住：四处都不再出现 python，且老库里的残留会被 `ensure_defaults()`
（`_drop_retired_tools`）清掉，同时不动别名与用户显式收窄的清单。
"""
from __future__ import annotations

import re
from pathlib import Path

from services.agent_zero.stores import zero_store
from services.agent_zero.tools import registry


def test_python_is_not_a_known_tool():
    assert "python" not in registry.known_codes()
    # canonical 不会把它归一成别的工具（否则会把 python 当成 shell 执行）
    assert registry.canonical("python") == "python"


def test_python_not_in_default_tools():
    assert "python" not in zero_store.DEFAULT_TOOLS
    # 默认清单里的每个 code 都必须真的存在（否则新角色一建出来就带死工具）
    unknown = [t for t in zero_store.DEFAULT_TOOLS if t not in set(registry.known_codes())]
    assert not unknown, f"默认工具清单里有 registry 不存在的工具：{unknown}"


def test_codegraph_invalidation_whitelist_has_no_python():
    from services.agent_zero.runtime.zero import dispatch

    src = Path(dispatch.__file__).read_text(encoding="utf-8")
    block = re.search(r"if canon not in \((.*?)\)", src, re.S)
    assert block, "找不到 _invalidate_codegraph 的白名单"
    assert '"python"' not in block.group(1)


def test_sandbox_only_runs_shell():
    """沙箱不该再有 run_python：Python 一律由模型写成 shell 命令自行执行。

    留着 run_python（以及给 `sys.executable` 兜底的 python_executable）等于
    留一条没人走的执行通道：打包成 .app 后它还会「再启动一个 Atlas」。
    """
    from services.agent_zero.runtime.zero.sandbox import subprocess_sandbox

    assert not hasattr(subprocess_sandbox.SubprocessSandbox, "run_python")
    assert not hasattr(subprocess_sandbox, "python_executable")
    assert hasattr(subprocess_sandbox.SubprocessSandbox, "run_shell")


def test_drop_retired_tools_cleans_legacy_envelope(monkeypatch):
    """老库里角色 envelope 还带 python：一次 ensure_defaults 之后要消失。"""
    roles = [
        {"id": "r1", "group_id": "ws", "name": "A", "persona": "", "expertise": "",
         "avatar": "", "envelope": {"tools": ["read_file", "python", "shell"]}},
        {"id": "r2", "group_id": "ws", "name": "B", "persona": "", "expertise": "",
         "avatar": "", "envelope": {"tools": ["read_file", "read_document", "python"]}},
    ]
    saved: list = []

    monkeypatch.setattr(zero_store, "list_roles", lambda limit=100: list(roles))
    monkeypatch.setattr(zero_store, "upsert_role", lambda r: saved.append(r))

    cleaned = zero_store._drop_retired_tools()

    assert cleaned == 2
    assert [r["id"] for r in saved] == ["r1", "r2"]
    assert saved[0]["envelope"]["tools"] == ["read_file", "shell"]
    # 别名先归一，不是简单删除：read_document → read_office
    assert saved[1]["envelope"]["tools"] == ["read_file", "read_office"]


def test_drop_retired_tools_is_idempotent(monkeypatch):
    """已经干净的 envelope 不该被反复写库。"""
    roles = [{"id": "r1", "group_id": "ws", "name": "A", "persona": "", "expertise": "",
              "avatar": "", "envelope": {"tools": ["read_file", "shell"]}}]
    saved: list = []
    monkeypatch.setattr(zero_store, "list_roles", lambda limit=100: list(roles))
    monkeypatch.setattr(zero_store, "upsert_role", lambda r: saved.append(r))

    assert zero_store._drop_retired_tools() == 0
    assert saved == []


def test_drop_retired_tools_keeps_mcp_tools(monkeypatch):
    """MCP 工具（mcp__<server>__<tool>）不在 registry 里，但绝不能当残留删掉。"""
    roles = [{"id": "r1", "group_id": "ws", "name": "A", "persona": "", "expertise": "",
              "avatar": "",
              "envelope": {"tools": ["read_file", "python",
                                     "mcp__github__create_issue", "mcp__fs__read"]}}]
    saved: list = []
    monkeypatch.setattr(zero_store, "list_roles", lambda limit=100: list(roles))
    monkeypatch.setattr(zero_store, "upsert_role", lambda r: saved.append(r))

    assert zero_store._drop_retired_tools() == 1
    assert saved[0]["envelope"]["tools"] == [
        "read_file", "mcp__github__create_issue", "mcp__fs__read"]


def test_drop_retired_tools_keeps_user_narrowed_list(monkeypatch):
    """用户显式收窄的清单（只剩 read_file）不该被补回或改写。"""
    roles = [{"id": "r1", "group_id": "ws", "name": "A", "persona": "", "expertise": "",
              "avatar": "", "envelope": {"tools": ["read_file"]}}]
    saved: list = []
    monkeypatch.setattr(zero_store, "list_roles", lambda limit=100: list(roles))
    monkeypatch.setattr(zero_store, "upsert_role", lambda r: saved.append(r))

    assert zero_store._drop_retired_tools() == 0
    assert saved == []
