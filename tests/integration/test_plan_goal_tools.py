"""目标 / 任务清单工具的后端行为测试（真实 sqlite + API 建会话）。"""

from __future__ import annotations


def _new_conversation(client):
    API = "/api/agent-zero"
    ws = client.get(f"{API}/workspaces").json()["data"]["items"]
    conv = client.post(f"{API}/conversations",
                       json={"workspace_id": ws[0]["id"], "title": "plan-tools"})
    assert conv.status_code == 200, conv.text
    return conv.json()["data"]["id"]


def test_goal_report_progress_blocked_and_manage(client):
    from services.agent_zero.task_layer import goal_service

    cid = _new_conversation(client)
    goal_service.set_goal(cid, "发布 2.0.9")

    out = goal_service.report_progress(
        cid, "progress", "完成打包", progress_pct=40,
        next_steps=["验证 DMG", "提交 SVN"], blockers=["等待用户确认"],
        evidence="pytest 全部通过")
    assert "完成度 40%" in out["note"]
    assert "下一步" in out["note"] and "阻塞" in out["note"]
    assert "pytest 全部通过" in out["note"]

    blocked = goal_service.report_progress(cid, "blocked", "需要 API key")
    assert goal_service.is_blocked(blocked)
    assert blocked["note"].startswith(goal_service.BLOCKED_PREFIX)
    # 等待用户时不再自动续跑
    assert goal_service.after_run_finished(
        cid, "run-blocked", {"prompt_tokens": 1, "completion_tokens": 1}) is None

    resumed = goal_service.manage(cid, {"action": "resume", "note": "已提供 key"})
    assert resumed["ok"] and not goal_service.is_blocked(resumed["goal"])

    noted = goal_service.manage(cid, {"action": "note", "note": "继续写测试"})
    assert noted["ok"] and "继续写测试" in noted["goal"]["note"]
    blocked2 = goal_service.manage(cid, {"action": "blocked", "note": "等发布窗口"})
    assert blocked2["ok"] and goal_service.is_blocked(blocked2["goal"])

    upd = goal_service.manage(cid, {"action": "update", "token_budget": 500,
                                    "max_runs": 3})
    assert upd["goal"]["token_budget"] == 500
    assert upd["goal"]["max_runs"] == 3

    status = goal_service.manage(cid, {"action": "status"})
    assert status["ok"] and status["goal"]["status"] == "active"

    closed = goal_service.manage(cid, {"action": "close", "note": "用户结束"})
    assert closed["goal"]["status"] == "closed"


def test_todo_write_replaces_whole_list(client):
    """todo_write：整份覆盖、状态对齐 DSH、最多一条 in_progress。"""
    from services.agent_zero.services import todo_service

    cid = _new_conversation(client)
    out = todo_service.handle_tool(cid, {"todos": [
        {"content": "调研", "status": "in_progress"},
        {"content": "实现", "status": "pending"},
        {"content": "验证", "status": "pending"},
    ]})
    assert out["ok"] and out["counts"]["total"] == 3
    assert [it["content"] for it in out["items"]] == ["调研", "实现", "验证"]
    assert out["items"][0]["status"] == "in_progress"

    # 整份覆盖：删掉「调研」，把「实现」推进为进行中（多余 in_progress 降级）
    out = todo_service.handle_tool(cid, {"todos": [
        {"content": "实现", "status": "in_progress"},
        {"content": "验证", "status": "in_progress"},
    ]})
    assert [it["content"] for it in out["items"]] == ["实现", "验证"]
    assert out["counts"] == {"pending": 1, "in_progress": 1, "completed": 0, "total": 2}
    assert out["items"][0]["status"] == "in_progress"
    assert out["items"][1]["status"] == "pending"

    # 完成一条：只有状态变化，id 保持不变（界面不闪）
    first_id = out["items"][0]["id"]
    out = todo_service.handle_tool(cid, {"todos": [
        {"content": "实现", "status": "completed"},
        {"content": "验证", "status": "pending"},
    ]})
    assert out["items"][0]["id"] == first_id
    assert out["items"][0]["status"] == "completed"

    # 空清单 = 清空
    out = todo_service.handle_tool(cid, {"todos": []})
    assert out["items"] == [] and out["counts"]["total"] == 0


def test_todo_write_rejects_bad_payload(client):
    from services.agent_zero.services import todo_service

    cid = _new_conversation(client)
    out = todo_service.handle_tool(cid, {"todos": "不是数组"})
    assert "error" in out
