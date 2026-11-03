from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """每个测试独立 sqlite + 配置文件，避免互相污染全局引擎/worker。"""
    from services.agent_zero import database
    from services.agent_zero.task_layer import conv_worker

    database._engine = None
    database._SessionLocal = None
    conv_worker._worker = None

    cfg = yaml.safe_load((ROOT / "config" / "agent_zero" / "config.yaml").read_text(encoding="utf-8"))
    cfg.setdefault("sqlalchemy", {})["db_path"] = str(tmp_path / "agent_zero.db")
    cfg.setdefault("server", {})["port"] = 0
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("AI_PLATFORM_CONFIG_YAML", str(config_path))
    monkeypatch.setenv("AGENT_ZERO_LOGS_DIR", str(tmp_path / "logs"))
    yield {"config": config_path, "tmp": tmp_path, "root": ROOT}

    database._engine = None
    database._SessionLocal = None
    conv_worker._worker = None


@pytest.fixture()
def client(app_env):
    from starlette.testclient import TestClient

    from services.agent_zero.webapp import create_app

    app = create_app(
        config_yaml=str(app_env["config"]),
        web_dir=str(app_env["root"] / "web"),
        logs_dir=str(app_env["tmp"] / "logs"),
    )
    with TestClient(app) as test_client:
        yield test_client
