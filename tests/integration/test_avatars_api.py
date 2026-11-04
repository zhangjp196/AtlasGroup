"""头像上传：角色 / 部门 / 群组共用，支持多种图片格式。"""
from __future__ import annotations

import base64

API = "/api/agent-zero"

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
    "DwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_upload_and_serve_avatar(client, monkeypatch, tmp_path):
    from services.agent_zero.services import avatar_service

    monkeypatch.setattr(avatar_service, "_root", lambda: tmp_path / "avatars")
    res = client.post(f"{API}/avatars", data={"scope": "role"},
                      files={"file": ("me.png", PNG_1PX, "image/png")})
    assert res.status_code == 200, res.text
    out = res.json()["data"]
    assert out["url"].startswith("/api/agent-zero/avatars/role/avatar-")
    assert out["mime"] == "image/png"

    served = client.get(out["url"])
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/png")
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.content == PNG_1PX


def test_upload_svg_and_reject_bad_files(client, monkeypatch, tmp_path):
    from services.agent_zero.services import avatar_service

    monkeypatch.setattr(avatar_service, "_root", lambda: tmp_path / "avatars")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'
    ok = client.post(f"{API}/avatars", data={"scope": "department"},
                     files={"file": ("logo.svg", svg, "image/svg+xml")})
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["mime"] == "image/svg+xml"

    bad = client.post(f"{API}/avatars", data={"scope": "role"},
                      files={"file": ("x.txt", b"not an image", "text/plain")})
    assert bad.status_code == 400

    bad_scope = client.post(f"{API}/avatars", data={"scope": "nope"},
                            files={"file": ("x.png", PNG_1PX, "image/png")})
    assert bad_scope.status_code == 400


def test_entities_store_avatar(client, monkeypatch, tmp_path):
    from services.agent_zero.services import avatar_service

    monkeypatch.setattr(avatar_service, "_root", lambda: tmp_path / "avatars")

    def upload(scope):
        res = client.post(f"{API}/avatars", data={"scope": scope},
                          files={"file": ("a.png", PNG_1PX, "image/png")})
        return res.json()["data"]["url"]

    role_avatar = upload("role")
    role = client.post(f"{API}/roles", json={
        "name": "张三", "title": "工程师", "avatar": role_avatar,
    }).json()["data"]
    assert role["avatar"] == role_avatar

    dept_avatar = upload("department")
    dept = client.post(f"{API}/departments", json={
        "name": "产品部", "avatar": dept_avatar,
    }).json()["data"]
    assert dept["avatar"] == dept_avatar

    team_avatar = upload("team")
    team = client.post(f"{API}/teams", json={
        "name": "头像群", "avatar": team_avatar,
    }).json()["data"]
    assert team["avatar"] == team_avatar
    listed = client.get(f"{API}/teams").json()["data"]["items"]
    assert next(t for t in listed if t["id"] == team["id"])["avatar"] == team_avatar
