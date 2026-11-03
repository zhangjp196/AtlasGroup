"""扩展机制 API：斜杠命令 / 钩子的增删查。"""
from __future__ import annotations


def _new_workspace(client, tmp_path, name="ext-ws"):
    folder = tmp_path / name
    folder.mkdir()
    res = client.post("/api/agent-zero/workspaces", json={
        "name": name, "path": str(folder),
    })
    assert res.status_code == 200, res.text
    return res.json()["data"]["id"], folder


def test_commands_api_lists_builtins_and_crud(client, app_env, tmp_path):
    res = client.get("/api/agent-zero/app/commands")
    assert res.status_code == 200
    names = {c["name"] for c in res.json()["data"]["commands"]}
    assert {"init", "review", "test", "commit"} <= names

    # 纯聊天工作区没有目录，不能写命令
    bad = client.post("/api/agent-zero/app/commands", json={
        "name": "deploy", "body": "deploy $ARGUMENTS", "workspace_id": "default",
    })
    assert bad.status_code == 400

    ws_id, folder = _new_workspace(client, tmp_path)
    res = client.post("/api/agent-zero/app/commands", json={
        "name": "deploy",
        "body": "部署到 $ARGUMENTS",
        "description": "部署",
        "argument_hint": "[env]",
        "workspace_id": ws_id,
    })
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert (folder / ".atlas" / "commands" / "deploy.md").exists()
    assert any(c["name"] == "deploy" for c in data["commands"])

    res = client.delete(f"/api/agent-zero/app/commands/deploy?workspace_id={ws_id}")
    assert res.status_code == 200
    assert not (folder / ".atlas" / "commands" / "deploy.md").exists()


def test_hooks_api_template_add_and_delete(client, app_env, tmp_path):
    ws_id, folder = _new_workspace(client, tmp_path, "hook-ws")
    res = client.post("/api/agent-zero/app/hooks/template",
                      json={"workspace_id": ws_id})
    assert res.status_code == 200, res.text
    assert (folder / ".atlas" / "hooks.json").exists()

    res = client.post("/api/agent-zero/app/hooks", json={
        "event": "PreToolUse",
        "matcher": "shell",
        "command": "echo guard",
        "timeout": 5,
        "workspace_id": ws_id,
    })
    assert res.status_code == 200, res.text
    hooks = res.json()["data"]["hooks"]
    assert len(hooks) == 1
    assert hooks[0]["event"] == "PreToolUse"
    assert hooks[0]["matcher"] == "shell"

    res = client.delete(
        f"/api/agent-zero/app/hooks?event=PreToolUse&index=0&workspace_id={ws_id}")
    assert res.status_code == 200
    assert res.json()["data"]["hooks"] == []


def test_hooks_api_rejects_unknown_event(client, app_env, tmp_path):
    ws_id, _folder = _new_workspace(client, tmp_path, "bad-hook-ws")
    res = client.post("/api/agent-zero/app/hooks", json={
        "event": "Nope", "command": "echo x", "workspace_id": ws_id,
    })
    assert res.status_code == 400


def test_command_prompt_kept_out_of_transcript_but_in_model_history(
        client, app_env, tmp_path):
    """转录显示 /check，模型历史用展开后的 prompt。"""
    from services.agent_zero.services import command_service
    from services.agent_zero.task_layer import conversation

    ws_id, folder = _new_workspace(client, tmp_path, "prompt-ws")
    (folder / "src").mkdir()
    (folder / "src" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    (folder / ".atlas" / "commands").mkdir(parents=True)
    (folder / ".atlas" / "commands" / "check.md").write_text(
        "---\ndescription: 检查\n---\n检查 $ARGUMENTS 并读取 @src/a.py\n",
        encoding="utf-8")
    command_service.invalidate()

    res = client.post("/api/agent-zero/conversations", json={"workspace_id": ws_id})
    assert res.status_code == 200, res.text
    conv_id = res.json()["data"]["id"]

    resolved = command_service.resolve(ws_id, "/check src/a.py")
    assert resolved
    conversation.send_message(conv_id, "/check src/a.py", "local",
                              prompt=resolved["prompt"])

    history = conversation._history_messages(conv_id)
    model_text = "\n".join(str(m.get("content") or "") for m in history)
    assert "[自定义命令 /check]" in model_text
    assert "VALUE = 1" in model_text

    rows = conversation.list_messages(conv_id)
    assert any(str(r.get("content") or "").strip() == "/check src/a.py"
               for r in rows)


def test_command_prompt_available_for_interventions(client, app_env, tmp_path):
    """run 执行中插入的 /命令 也要把展开后的 prompt 作为干预正文。"""
    from services.agent_zero.services import command_service
    from services.agent_zero.task_layer import conversation

    ws_id, folder = _new_workspace(client, tmp_path, "intervene-ws")
    (folder / ".atlas" / "commands").mkdir(parents=True)
    (folder / ".atlas" / "commands" / "focus.md").write_text(
        "---\ndescription: 聚焦\n---\n只做：$ARGUMENTS\n", encoding="utf-8")
    command_service.invalidate()
    res = client.post("/api/agent-zero/conversations", json={"workspace_id": ws_id})
    conv_id = res.json()["data"]["id"]
    resolved = command_service.resolve(ws_id, "/focus 登录功能")
    conversation.send_message(conv_id, "/focus 登录功能", "local",
                              prompt=resolved["prompt"])
    rows = conversation._recent_exploration_rows(conv_id)
    hit = [r for r in rows if r["content"] == "/focus 登录功能"]
    assert hit and "只做：登录功能" in hit[0]["prompt"]
