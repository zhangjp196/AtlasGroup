from __future__ import annotations

import io
import time
import zipfile

API = "/api/agent-zero"


def test_health_metrics_backup_and_diagnostics(client, app_env):
    from services.agent_zero import database

    health = client.get("/healthz")
    assert health.status_code == 200, health.text
    body = health.json()
    assert body["ok"] is True
    assert body["db"]["schema_version"] == database.SCHEMA_VERSION
    assert body["worker"]["ok"] is True

    metrics = client.get(f"{API}/app/metrics").json()["data"]
    assert "runs" in metrics and "success_rate" in metrics["runs"]
    assert "backups" in metrics

    created = client.post(f"{API}/app/backups")
    assert created.status_code == 200, created.text
    name = created.json()["data"]["name"]
    assert (app_env["tmp"] / "backups" / name).is_file()
    listed = client.get(f"{API}/app/backups").json()
    assert listed["data"]["count"] >= 1

    resp = client.get(f"{API}/app/diagnostics")
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = set(zf.namelist())
    for need in ("meta.json", "health.json", "metrics.json",
                 "settings.json", "runs.json", "backups.json"):
        assert need in names, need
    settings_text = zf.read("settings.json").decode("utf-8")
    assert "sk-" not in settings_text


def test_process_panel_end_to_end(client):
    """真实 app 上跑进程面板：列表能看到后台工具，停它不影响会话。

    这条覆盖 router 注册 + 依赖注入 + 序列化的完整链路（单测里是直接调
    handler，注册错前缀这类问题只有端到端能发现）。
    """
    import asyncio
    import json

    from services.agent_zero.task_layer import tool_jobs

    API = "/api/agent-zero"
    ws = client.get(f"{API}/workspaces").json()["data"]["items"]
    wid = ws[0]["id"]
    conv = client.post(f"{API}/conversations",
                       json={"workspace_id": wid, "title": "proc-e2e"})
    assert conv.status_code == 200, conv.text
    cid = conv.json()["data"]["id"]

    async def slow():
        await asyncio.sleep(60)

    loop = asyncio.new_event_loop()
    try:
        task = loop.create_task(slow())
        job = tool_jobs.adopt(
            conversation_id=cid, run_id="", name="shell",
            args={"cmd": "bash build_macos.sh 2>&1 | tail -25"}, task=task)

        listed = client.get(f"{API}/conversations/{cid}/processes")
        assert listed.status_code == 200, listed.text
        items = listed.json()["data"]["items"]
        row = next(i for i in items if i["id"] == job["id"])
        assert row["kind"] == "tool"
        assert "build_macos.sh" in row["args"]["cmd"]
        assert row["status"] == "running"

        stopped = client.post(f"{API}/processes/{job['id']}/stop?kind=tool")
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["data"]["stopped"] is True
        time.sleep(0.3)
        assert tool_jobs.get(job["id"])["status"] == "killed"

        # 停完之后列表里不再有它（面板据此自动隐藏）
        after = client.get(f"{API}/conversations/{cid}/processes").json()["data"]["items"]
        assert all(i["id"] != job["id"] for i in after if i["status"] == "running")

        # 未知进程 / 未知会话都是 404，不抛 500
        assert client.post(f"{API}/processes/job-nope/stop").status_code == 404
        assert client.get(f"{API}/conversations/conv-nope/processes").status_code == 404
    finally:
        loop.close()
