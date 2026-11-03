"""V3 新界面静态入口：/v3 默认入口，会话为嵌入式多标签。"""
from __future__ import annotations


def test_v3_shell_is_served(client):
    res = client.get("/v3/")
    assert res.status_code == 200
    assert "AtlasX" in res.text
    assert "/v3/app.js" in res.text
    assert "chatShellV3" in res.text


def test_v3_assets_are_served(client):
    assert client.get("/v3/app.js").status_code == 200
    assert client.get("/v3/app.css").status_code == 200


def test_default_entry_is_v3(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "/v3/app.js" in res.text
    assert "chatShellV3" in res.text


def test_old_shell_is_gone(client):
    res = client.get("/classic")
    assert res.status_code == 410
    assert "旧界面已移除" in res.text


def test_v2_settings_host_is_available(client):
    res = client.get("/classic?settings=1")
    assert res.status_code == 200
    assert "/assets/app.js" in res.text
