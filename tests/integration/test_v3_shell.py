"""3.7 唯一前端入口：Vue/Vite 产物，旧 V3 / classic / next 全部移除。"""
from __future__ import annotations

import os


def test_root_serves_vue_index(client):
    res = client.get("/")
    if res.status_code == 503:
        # 干净检出尚未构建前端时允许明确提示，但不能回退到旧页面
        assert "前端尚未构建" in res.text
        return
    assert res.status_code == 200
    assert "AtlasX" in res.text
    assert "/static/" in res.text
    assert "chatShellV3" not in res.text


def test_old_frontend_routes_are_gone(client):
    for path in ("/classic", "/classic?settings=1", "/v3", "/v3/", "/next", "/next/", "/v3/app.js", "/assets/app.js"):
        assert client.get(path).status_code == 404, path


def test_spa_fallback_serves_vue_index(client):
    if not os.path.exists("web/dist/index.html"):
        return
    res = client.get("/conv/example")
    assert res.status_code == 200
    assert "/static/" in res.text
    assert "chatShellV3" not in res.text
