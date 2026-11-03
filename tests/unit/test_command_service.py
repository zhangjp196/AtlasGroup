"""自定义斜杠命令：解析 / 展开 / 覆盖 / 写入。"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.agent_zero.services import command_service


@pytest.fixture()
def cmd_env(tmp_path, monkeypatch):
    ws_root = tmp_path / "ws"
    (ws_root / ".atlas" / "commands").mkdir(parents=True)
    (ws_root / "src").mkdir()
    (ws_root / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    from services.agent_zero.services import workspace_service

    def _resolve(_ws, rel):
        target = (ws_root / str(rel or ".")).resolve()
        target.relative_to(ws_root.resolve())
        return target

    monkeypatch.setattr(workspace_service, "resolve_workspace_path", _resolve)
    monkeypatch.setattr(command_service, "workspace_commands_dirs",
                        lambda _ws: [ws_root / ".atlas" / "commands"])
    monkeypatch.setattr(command_service, "global_commands_dir",
                        lambda: tmp_path / "none-global")
    monkeypatch.setattr(command_service, "user_commands_dir",
                        lambda: tmp_path / "none-user")
    command_service.invalidate()
    yield ws_root
    command_service.invalidate()


def test_builtin_commands_listed(cmd_env):
    names = {c["name"] for c in command_service.list_commands("ws")}
    assert {"init", "review", "test", "commit"} <= names


def test_file_command_expands_arguments_and_files(cmd_env):
    (cmd_env / ".atlas" / "commands" / "check.md").write_text(
        "---\n"
        "description: 检查文件\n"
        "argument-hint: \"[path]\"\n"
        "---\n"
        "检查 $1，范围：$ARGUMENTS\n读取 @src/app.py\n",
        encoding="utf-8",
    )
    command_service.invalidate()
    resolved = command_service.resolve("ws", "/check src/app.py --strict")
    assert resolved is not None
    assert resolved["name"] == "check"
    assert "检查 src/app.py" in resolved["prompt"]
    assert "--strict" in resolved["prompt"]
    assert "print('hi')" in resolved["prompt"]
    assert "src/app.py" in resolved["files"]


def test_workspace_command_overrides_builtin(cmd_env):
    (cmd_env / ".atlas" / "commands" / "review.md").write_text(
        "---\ndescription: 自定义审查\n---\n只做自定义审查 $ARGUMENTS\n",
        encoding="utf-8",
    )
    command_service.invalidate()
    resolved = command_service.resolve("ws", "/review now")
    assert resolved["description"] == "自定义审查"
    assert "只做自定义审查 now" in resolved["prompt"]


def test_unknown_slash_text_is_not_a_command(cmd_env):
    assert command_service.resolve("ws", "/Users/me/code") is None
    assert command_service.resolve("ws", "hello /review") is None


def test_create_and_delete_workspace_command(cmd_env):
    data = command_service.create_command(
        "ws", "deploy", "部署到 $ARGUMENTS", description="部署")
    path = Path(data["path"])
    assert path.exists()
    assert path.parent == cmd_env / ".atlas" / "commands"
    assert command_service.resolve("ws", "/deploy prod")["name"] == "deploy"
    command_service.delete_command("ws", "deploy")
    assert not path.exists()
    assert command_service.resolve("ws", "/deploy prod") is None


def test_invalid_command_name_rejected(cmd_env):
    with pytest.raises(ValueError):
        command_service.create_command("ws", "../evil", "x")

