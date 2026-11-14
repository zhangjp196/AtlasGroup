"""Crash recovery keeps partial output and frees the run's shared capacity."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from services.agent_zero.database import get_session_local
from services.agent_zero.stores.zero_store import _q
from services.agent_zero.task_layer import run_service


def test_process_recovery_persists_partial_reply_once_and_releases_reservations(client):
    conversation_id = client.post(
        "/api/agent-zero/conversations", json={}).json()["data"]["id"]
    run = run_service.create_run(
        conversation_id, "local", content="完成这个长任务", mode="autonomous")
    run_id = run["id"]

    # Leave a durable running row without starting a worker; this is the state
    # a hard process exit leaves behind when startup recovery executes.
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('conversation_runs')} SET status = 'running' WHERE id = :id"),
            {"id": run_id},
        )
        db.commit()

    reservation_id = run_service.reserve_subagent_slot(run_id)
    assert reservation_id
    run_service.append_event(run_id, "delta", {"delta": "已完成第一步", "round": 0})
    run_service.append_event(run_id, "thinking", {"thinking": "检查下一步", "round": 0})
    run_service.append_event(run_id, "tool_call", {
        "tool_call": {
            "id": "tool-1", "name": "write_file", "args": {"path": "src/app.py"},
            "status": "preparing", "round": 0,
        },
    })

    assert run_service.recover_running_runs("测试模拟进程重启") == 1

    recovered = run_service.get_run(run_id)
    assert recovered["status"] == run_service.STATUS_FAILED
    assert recovered["error"] == "测试模拟进程重启"
    events = run_service.list_all_events(run_id)
    assert [event["_kind"] for event in events][-2:] == ["interrupted", "error"]
    assert run_service.reserve_subagent_slot(run_id) is None

    messages_response = client.get(
        f"/api/agent-zero/conversations/{conversation_id}/messages")
    assert messages_response.status_code == 200, messages_response.text
    messages = messages_response.json()["data"]
    partial = [message for message in messages if message.get("run_id") == run_id]
    assert len(partial) == 1
    assert partial[0]["content"] == "已完成第一步"
    assert partial[0]["interrupted"] is True
    assert partial[0]["error"] == "测试模拟进程重启"
    assert [step["kind"] for step in partial[0]["trace"]] == [
        "text", "thinking", "tool",
    ]

    # A repeated recovery is a no-op: it neither duplicates the transcript row
    # nor emits another terminal event.
    assert run_service.recover_running_runs("再次启动") == 0
    assert len([message for message in client.get(
        f"/api/agent-zero/conversations/{conversation_id}/messages").json()["data"]
        if message.get("run_id") == run_id]) == 1
    assert len(run_service.list_all_events(run_id)) == len(events)


def test_new_app_lifespan_recovers_run_left_by_previous_process(app_env, monkeypatch):
    """A second app startup must recover durable work without direct recovery calls."""
    from starlette.testclient import TestClient

    from services.agent_zero.task_layer import conv_worker, conversation
    from services.agent_zero.webapp import create_app

    recovery_calls = []
    recover = run_service.recover_running_runs

    def track_recovery(reason="服务重启，运行中断"):
        recovery_calls.append(reason)
        return recover(reason)

    monkeypatch.setattr(run_service, "recover_running_runs", track_recovery)

    def build_app():
        return create_app(
            config_yaml=str(app_env["config"]),
            web_dir=str(app_env["root"] / "web"),
            logs_dir=str(app_env["tmp"] / "logs"),
        )

    with TestClient(build_app()) as first_process:
        conversation_id = first_process.post(
            "/api/agent-zero/conversations", json={}).json()["data"]["id"]

    # Seed durable in-flight state after the first worker has shut down, as if
    # the process had exited while a long task was running.
    run = run_service.create_run(
        conversation_id, "local", content="重启后接着处理", mode="autonomous")
    run_id = run["id"]
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('conversation_runs')} SET status = 'running' WHERE id = :id"),
            {"id": run_id},
        )
        db.commit()
    assert run_service.reserve_subagent_slot(run_id)
    run_service.append_event(run_id, "delta", {"delta": "已完成持久化步骤", "round": 0})
    run_service.append_event(run_id, "tool_call", {
        "tool_call": {"id": "tool-restart", "name": "write_file",
                      "args": {"path": "src/resume.py"},
                      "status": "preparing", "round": 0},
    })

    # A fresh worker object represents the process-local state being lost.
    conv_worker._worker = None
    with TestClient(build_app()) as restarted_process:
        recovered = run_service.get_run(run_id)
        assert recovered["status"] == run_service.STATUS_FAILED
        assert recovered["error"] == "服务重启，运行中断"
        partial = [item for item in restarted_process.get(
            f"/api/agent-zero/conversations/{conversation_id}/messages"
        ).json()["data"] if item.get("run_id") == run_id]
        assert len(partial) == 1
        assert partial[0]["content"] == "已完成持久化步骤"
        assert partial[0]["interrupted"] is True
        assert run_service.reserve_subagent_slot(run_id) is None

    # Exactly one recovery scan per application startup (not again in worker start).
    assert recovery_calls == ["服务重启，运行中断", "服务重启，运行中断"]

    # Keep the existing public façade available to routes and workers.
    assert conversation.get_conversation(conversation_id)


def test_recovery_retries_after_exit_between_reply_persistence_and_status_update(
        client, monkeypatch):
    conversation_id = client.post(
        "/api/agent-zero/conversations", json={}).json()["data"]["id"]
    run_id = run_service.create_run(
        conversation_id, "local", content="容错恢复", mode="autonomous")["id"]
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        db.execute(
            text(f"UPDATE {_q('conversation_runs')} SET status = 'running' WHERE id = :id"),
            {"id": run_id},
        )
        db.commit()
    assert run_service.reserve_subagent_slot(run_id)
    run_service.append_event(run_id, "delta", {"delta": "保留这段输出", "round": 0})

    finalize = run_service._finalize_interrupted

    def finalize_then_exit(run_ids, reason):
        finalize(run_ids, reason)
        raise SystemExit("模拟在持久化回复后进程退出")

    monkeypatch.setattr(run_service, "_finalize_interrupted", finalize_then_exit)
    with pytest.raises(SystemExit):
        run_service.recover_running_runs("模拟崩溃窗口")
    assert run_service.get_run(run_id)["status"] == run_service.STATUS_RUNNING

    monkeypatch.setattr(run_service, "_finalize_interrupted", finalize)
    event_count = len(run_service.list_all_events(run_id))
    assert run_service.recover_running_runs("模拟崩溃窗口") == 1
    assert run_service.get_run(run_id)["status"] == run_service.STATUS_FAILED
    assert len(run_service.list_all_events(run_id)) == event_count
    assert run_service.reserve_subagent_slot(run_id) is None
    partial = [message for message in client.get(
        f"/api/agent-zero/conversations/{conversation_id}/messages"
    ).json()["data"] if message.get("run_id") == run_id]
    assert len(partial) == 1
    assert partial[0]["content"] == "保留这段输出"
