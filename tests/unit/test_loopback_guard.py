"""监听地址安全边界：非回环绑定必须打 warn（接口没有鉴权）。

背景：`services/shared/auth.py` 只有 `LOCAL_USER_ID`，全部路由都不校验身份。
默认 `server.host = 127.0.0.1` 时没问题，但改成 `0.0.0.0` 后
`PUT/DELETE /api/agent-zero/workspace/file` 等写接口就对同网段裸奔。
这里锁住 `webapp.serve()` 的提示行为，防止有人把边界悄悄拆掉。
"""

from __future__ import annotations

import logging

import pytest
import yaml

from services.agent_zero import webapp


def _run_serve(tmp_path, monkeypatch, host):
    """跑 serve()，但在 bind_socket 处短路，避免真的监听端口。"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"server": {"host": host, "port": 0, "auto_port": False}}),
        encoding="utf-8")

    def _fake_bind_socket(host_, port_, *, auto=True, tries=1):
        raise RuntimeError("stop-here")

    class _FakeApp:
        pass

    monkeypatch.setattr(webapp, "bind_socket", _fake_bind_socket)
    with pytest.raises(RuntimeError, match="stop-here"):
        webapp.serve(
            _FakeApp(),
            config_yaml=str(cfg_path),
            runtime_file=str(tmp_path / "runtime.json"),
        )


def test_non_loopback_host_warns(tmp_path, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger=webapp.logger.name):
        _run_serve(tmp_path, monkeypatch, "0.0.0.0")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("没有鉴权" in r.getMessage() for r in warnings), \
        [r.getMessage() for r in warnings]


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_host_does_not_warn(tmp_path, monkeypatch, caplog, host):
    with caplog.at_level(logging.WARNING, logger=webapp.logger.name):
        _run_serve(tmp_path, monkeypatch, host)
    assert not [r for r in caplog.records
                if r.levelno == logging.WARNING and "没有鉴权" in r.getMessage()]
