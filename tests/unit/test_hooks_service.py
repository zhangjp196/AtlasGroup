"""工作区钩子：匹配 / 拒绝 / 参数替换 / 上下文注入 / 超时。"""
from __future__ import annotations

import asyncio
import json

import pytest

from services.agent_zero.services import hooks_service


@pytest.fixture()
def hook_env(tmp_path, monkeypatch):
    global_path = tmp_path / "global-hooks.json"
    ws_path = tmp_path / "ws" / ".atlas" / "hooks.json"
    ws_path.parent.mkdir(parents=True)
    monkeypatch.setattr(hooks_service, "global_hooks_path", lambda: global_path)
    monkeypatch.setattr(hooks_service, "workspace_hooks_path",
                        lambda _ws: ws_path)
    monkeypatch.setattr(hooks_service, "_cwd_for", lambda _ws: tmp_path)
    hooks_service.invalidate()
    yield {"global": global_path, "workspace": ws_path, "tmp": tmp_path}
    hooks_service.invalidate()


def _write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    hooks_service.invalidate()


def test_pre_tool_use_denies(hook_env):
    _write(hook_env["workspace"], {"PreToolUse": [{
        "matcher": "shell",
        "command": "printf '%s' '{\"decision\":\"deny\",\"reason\":\"blocked\"}'",
    }]})
    result = asyncio.run(hooks_service.pre_tool_use(
        "ws", "shell", {"cmd": "rm -rf /"}, run_id="run1"))
    assert result["decision"] == "deny"
    assert result["reason"] == "blocked"


def test_pre_tool_use_updates_input(hook_env):
    _write(hook_env["workspace"], {"PreToolUse": [{
        "command": "printf '%s' '{\"updatedInput\":{\"cmd\":\"echo safe\"}}'",
    }]})
    result = asyncio.run(hooks_service.pre_tool_use(
        "ws", "shell", {"cmd": "echo unsafe"}))
    assert result["tool_input"] == {"cmd": "echo safe"}
    assert result["decision"] == "allow"


def test_matcher_skips_other_tools(hook_env):
    _write(hook_env["workspace"], {"PreToolUse": [{
        "matcher": "^read_",
        "command": "printf '%s' '{\"decision\":\"deny\",\"reason\":\"no\"}'",
    }]})
    result = asyncio.run(hooks_service.pre_tool_use(
        "ws", "shell", {"cmd": "ls"}))
    assert result["decision"] == "allow"
    assert result["results"] == []


def test_post_tool_use_injects_context(hook_env):
    _write(hook_env["workspace"], {"PostToolUse": [{
        "command": "printf '%s' '{\"hookSpecificOutput\":{\"additionalContext\":\"remember this\"}}'",
    }]})
    result = asyncio.run(hooks_service.post_tool_use(
        "ws", "shell", {"cmd": "ls"}, "ok", True))
    assert result["context"] == "remember this"


def test_user_prompt_plain_stdout_is_context(hook_env):
    _write(hook_env["workspace"], {"UserPromptSubmit": [{
        "command": "echo extra-context",
    }]})
    result = asyncio.run(hooks_service.user_prompt_submit("ws", "hello"))
    assert result["blocked"] is False
    assert result["context"] == "extra-context"


def test_user_prompt_block(hook_env):
    _write(hook_env["global"], {"hooks": {"UserPromptSubmit": [{
        "command": "printf '%s' '{\"decision\":\"block\",\"reason\":\"secret\"}'",
    }]}})
    result = asyncio.run(hooks_service.user_prompt_submit("ws", "hello"))
    assert result["blocked"] is True
    assert result["reason"] == "secret"


def test_hook_timeout_does_not_block(hook_env):
    _write(hook_env["workspace"], {"PreToolUse": [{
        "command": "sleep 5", "timeout": 1,
    }]})
    result = asyncio.run(hooks_service.pre_tool_use(
        "ws", "shell", {"cmd": "ls"}))
    assert result["decision"] == "allow"
    assert result["results"][0]["timed_out"] is True
    assert result["results"][0]["ok"] is False


def test_add_and_remove_workspace_hook(hook_env):
    data = hooks_service.add_workspace_hook(
        "ws", "PreToolUse", "echo hi", matcher="shell", timeout=3)
    assert data["hooks"][0]["event"] == "PreToolUse"
    assert data["hooks"][0]["matcher"] == "shell"
    removed = hooks_service.remove_workspace_hook("ws", "PreToolUse", 0)
    assert removed["hooks"] == []


def test_workspace_hooks_run_after_global(hook_env):
    _write(hook_env["global"], {"PreToolUse": [{"command": "echo global"}]})
    _write(hook_env["workspace"], {"PreToolUse": [{"command": "echo workspace"}]})
    result = asyncio.run(hooks_service.pre_tool_use(
        "ws", "shell", {"cmd": "ls"}))
    commands = [r["command"] for r in result["results"]]
    assert commands == ["echo global", "echo workspace"]


def test_matcher_matches_tool_pack_name(hook_env):
    _write(hook_env["workspace"], {"PreToolUse": [{
        "matcher": "fs",
        "command": "printf '%s' '{\"decision\":\"deny\",\"reason\":\"fs blocked\"}'",
    }]})
    result = asyncio.run(hooks_service.pre_tool_use(
        "ws", "write_file", {"path": "a.py"}, tool_pack="fs"))
    assert result["decision"] == "deny"
    assert result["reason"] == "fs blocked"
