from __future__ import annotations

import os
import time

from starlette.testclient import TestClient

from services.agent_zero.services import terminal_service

API = "/api/agent-zero"


def test_terminal_and_live_script_use_conversation_workspace(app_env, monkeypatch):
    from services.agent_zero.webapp import create_app

    directory = app_env["tmp"] / "project with spaces"
    directory.mkdir()
    app = create_app(config_yaml=str(app_env["config"]), web_dir=str(app_env["root"] / "web"),
                     logs_dir=str(app_env["tmp"] / "logs"))
    opened = []
    monkeypatch.setattr(terminal_service, "open_terminal", opened.append)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 10000)) as local:
        workspace = local.post(f"{API}/workspaces", json={"path": str(directory)}).json()["data"]
        conv = local.post(f"{API}/conversations", json={"workspace_id": workspace["id"]}).json()["data"]
        target = {"conversation_id": conv["id"], "workspace_id": "ignored-for-conversation"}
        terminal = f"{API}/local/terminal"
        assert local.post(terminal, json=target, headers={"Origin": "https://example.com"}).status_code == 403
        assert local.post(terminal, json=target).json()["data"]["path"] == str(directory.resolve())
        assert opened == [directory.resolve()]

        jobs = f"{API}/local/background-jobs"
        script = "Write-Output (Get-Location).Path; Start-Sleep -Seconds 30" if os.name == "nt" else "pwd; sleep 30"
        created = local.post(jobs, json={**target, "command": script})
        assert created.status_code == 200, created.text
        url = f"{jobs}/{created.json()['data']['job_id']}"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            detail = local.get(url).json()["data"]
            if detail["stdout"]:
                break
            time.sleep(0.05)
        assert detail["status"] == "running"
        assert str(directory.resolve()) in detail["stdout"]
        assert local.post(f"{url}/stop").json()["data"]["stopped"]
        items = local.get(jobs, params={"conversation_id": conv["id"]}).json()["data"]["items"]
        assert items[0]["status"] == "killed"
        assert items[0]["finished_at"] >= items[0]["started_at"] > 0
        assert local.get(url).json()["data"]["stdout"] == detail["stdout"]

        script = "Write-Error failure; exit 7" if os.name == "nt" else "echo failure >&2; exit 7"
        job = local.post(jobs, json={"workspace_id": workspace["id"], "command": script}).json()["data"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            detail = local.get(f"{jobs}/{job['job_id']}").json()["data"]
            if detail["status"] != "running":
                break
            time.sleep(0.05)
        assert detail["status"] == "failed"
        assert detail["exit_code"] == 7
        assert "failure" in detail["stderr"]

