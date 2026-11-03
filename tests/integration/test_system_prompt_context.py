"""系统提示词接入对话：默认纯聊天工作区与项目约定文件的注入。"""
from __future__ import annotations

from services.agent_zero.task_layer import conversation

API = "/api/agent-zero"


def _role():
    return {"id": "r1", "name": "Atlas", "envelope": {}}


def test_default_workspace_prompt_is_chat_only(client, app_env):
    text = conversation._chat_system_prompt(_role(), workspace_id="default")
    assert "你是「Atlas」" in text
    assert "纯聊天工作区" in text
    assert "托管目录" in text
    assert "先读后改" in text
    assert "# 项目约定" not in text
    # 稳定准则只出现一次，动态段落没有重复拼装
    assert text.count("# 验证闭环") == 1
    assert text.count("# 工具使用") == 1


def test_workspace_rules_are_injected(client, app_env, tmp_path):
    folder = tmp_path / "rules-ws"
    folder.mkdir()
    (folder / "AGENTS.md").write_text(
        "使用 pytest。\n提交信息用中文。\n", encoding="utf-8")
    res = client.post(f"{API}/workspaces", json={
        "name": "rules", "path": str(folder)})
    assert res.status_code == 200, res.text
    ws_id = res.json()["data"]["id"]

    text = conversation._chat_system_prompt(_role(), workspace_id=ws_id)
    assert "# 项目约定" in text
    assert "使用 pytest。" in text
    assert "提交信息用中文。" in text
    assert "文件工具" in text
    assert "纯聊天工作区" not in text
