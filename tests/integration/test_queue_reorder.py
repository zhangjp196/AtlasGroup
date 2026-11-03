"""消息队列：上下自由移动（取代「插队」按钮）。

用户要求：「消息队列 去除插队按钮，改为支持上下自由移动」。

排队顺序的存储口径从 `priority`（0/1 二值）改成 `queue_pos`（整数序号）：

- 序号小先执行，同序号按 created_at 升序；
- 上移 / 下移后整体重写为 0..n-1（稠密、唯一），排序才确定；
- 队首再上移 / 队尾再下移是**空操作**（moved=False），不是错误；
- 旧库里的 priority=1 迁移成 queue_pos=-1（仍在队首），不丢原意。

这里锁定六件事：顺序、边界、只动 queued、移动后仍按新顺序被 worker 取到、
编辑排队消息保留位置、旧库升级路径。
"""
from __future__ import annotations

API = "/api/agent-zero"


def _conv(client) -> str:
    return client.post(f"{API}/conversations", json={}).json()["data"]["id"]


def _enqueue(conv_id: str, *contents: str) -> list:
    """直接建 queued run（不经 worker），保证稳定停在排队状态。"""
    from services.agent_zero.task_layer import run_service

    return [run_service.create_run(conv_id, "tester", content=c)["id"] for c in contents]


def _queue(client, conv_id: str) -> list:
    return client.get(f"{API}/conversations/{conv_id}/queue").json()["data"]["items"]


def _ids(items) -> list:
    return [it["id"] for it in items]


def _move(client, conv_id: str, run_id: str, direction: str):
    return client.post(f"{API}/conversations/{conv_id}/queue/{run_id}/move",
                       json={"direction": direction}).json()["data"]


def test_move_up_and_down_reorders(client):
    cid = _conv(client)
    a, b, c = _enqueue(cid, "第一条", "第二条", "第三条")
    assert _ids(_queue(client, cid)) == [a, b, c]

    # 第三条上移两次 → 队首
    assert _move(client, cid, c, "up")["moved"] is True
    assert _ids(_queue(client, cid)) == [a, c, b]
    assert _move(client, cid, c, "up")["moved"] is True
    assert _ids(_queue(client, cid)) == [c, a, b]

    # 再下移一位 → 回到中间
    assert _move(client, cid, c, "down")["moved"] is True
    assert _ids(_queue(client, cid)) == [a, c, b]


def test_move_at_boundary_is_noop_not_error(client):
    cid = _conv(client)
    a, b = _enqueue(cid, "一", "二")

    up = _move(client, cid, a, "up")
    assert up["moved"] is False
    assert _ids(up["items"]) == [a, b]

    down = _move(client, cid, b, "down")
    assert down["moved"] is False
    assert _ids(down["items"]) == [a, b]


def test_moving_running_run_is_rejected(client):
    """只有 queued 能移动：不存在的 run / 已终态 run 一律 404。"""
    cid = _conv(client)
    run_id = _enqueue(cid, "已取消")[0]
    from services.agent_zero.task_layer import run_service

    assert run_service.cancel_run(run_id) is True
    resp = client.post(f"{API}/conversations/{cid}/queue/{run_id}/move",
                       json={"direction": "up"})
    assert resp.status_code == 404, resp.text


def test_bad_direction_rejected(client):
    cid = _conv(client)
    run_id = _enqueue(cid, "一")[0]
    resp = client.post(f"{API}/conversations/{cid}/queue/{run_id}/move",
                       json={"direction": "left"})
    assert resp.status_code == 422, resp.text


def test_new_message_appends_to_tail_after_moves(client):
    """移动过之后再发消息，必须排到队尾（而不是插进被重写的序号中间）。"""
    cid = _conv(client)
    a, b, c = _enqueue(cid, "一", "二", "三")
    _move(client, cid, c, "up")          # [a, c, b]，序号 0/1/2
    d = _enqueue(cid, "四")[0]
    assert _ids(_queue(client, cid)) == [a, c, b, d]


def test_worker_picks_moved_order(client):
    """worker 取 run 用的 list_queued 必须跟界面顺序一致。"""
    cid = _conv(client)
    a, b, c = _enqueue(cid, "一", "二", "三")
    _move(client, cid, c, "up")
    _move(client, cid, c, "up")
    from services.agent_zero.task_layer import run_service

    queued_ids = [r["id"] for r in run_service.list_queued(limit=50)
                  if r["conversation_id"] == cid]
    assert queued_ids == [c, a, b]


def test_edit_queued_message_keeps_position(client):
    """编辑排队消息会换一条新 run：位置（序号）不变。"""
    from services.agent_zero.task_layer import run_service

    cid = _conv(client)
    a, b, c = _enqueue(cid, "一", "二", "三")
    _move(client, cid, c, "up")          # [a, c, b]，序号 0/1/2

    # edit_message 的实际做法：丢弃旧 run，用原 queue_pos 建新 run
    assert run_service.discard_run(c) is True
    new_id = run_service.create_run(cid, "tester", content="三（改过）",
                                    queue_pos=1)["id"]
    assert _ids(_queue(client, cid)) == [a, new_id, b]


def test_legacy_priority_column_migrates_to_queue_pos(app_env):
    """旧库升级：priority=1 的排队项应排到队首（迁移成 queue_pos=-1）。"""
    from sqlalchemy import text

    from services.agent_zero import database
    from services.agent_zero.stores import zero_store

    database.init_database()
    zero_store.ensure_defaults()
    engine = database.get_engine()
    conv_id = "conv-legacy"
    with engine.begin() as conn:
        # 模拟旧库：没有 queue_pos，有 priority
        conn.execute(text("ALTER TABLE conversation_runs DROP COLUMN queue_pos"))
        conn.execute(text(
            "ALTER TABLE conversation_runs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0"))
        conn.execute(text(
            "INSERT INTO conversations (id, workspace_id, role_id, title, created_by) "
            "VALUES (:id, 'default', 'default', 'legacy', 'local')"), {"id": conv_id})
        for rid, pri, offset in (("run-normal", 0, "-10 seconds"),
                                 ("run-jump", 1, "0 seconds")):
            conn.execute(text(
                "INSERT INTO conversation_runs (id, conversation_id, status, mode, cursor, "
                "created_by, created_at, priority) "
                "VALUES (:id, :cid, 'queued', 'chat', 0, 'local', "
                "datetime('now','localtime', :off), :pri)"),
                {"id": rid, "cid": conv_id, "pri": pri, "off": offset})

    # 再跑一次建表流程 = 真实的服务启动路径
    database.init_database()

    from services.agent_zero.task_layer import run_service

    order = [r["id"] for r in run_service.conversation_queue(conv_id)]
    assert order == ["run-jump", "run-normal"], order
