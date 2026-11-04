"""主题偏好的持久化：桌面端端口每次可能变，主题必须存在服务器而不是只存 localStorage。"""
from __future__ import annotations


def test_theme_prefs_roundtrip_keeps_layout(client):
    first = client.put("/api/agent-zero/app/theme", json={
        "theme": "ocean",
        "themeSchedule": {
            "mode": "daynight", "day": "light", "night": "dark",
            "interval": 15, "dayStart": "07:30", "nightStart": "21:00",
        },
    }).json()["data"]
    assert first["theme"] == "ocean"
    assert first["themeSchedule"]["dayStart"] == "07:30"

    got = client.get("/api/agent-zero/app/theme").json()["data"]
    assert got["theme"] == "ocean"
    assert got["themeSchedule"]["nightStart"] == "21:00"

    # 主题写入不能把同一份 ui 偏好里的布局字段冲掉。
    client.put("/api/agent-zero/app/ui", json={"prefs": {"theme": "ocean", "sidebarW": 320, "viewerWrap": True}})
    client.put("/api/agent-zero/app/theme", json={"theme": "sepia", "themeSchedule": {}})
    ui = client.get("/api/agent-zero/app/ui").json()["data"]
    assert ui["theme"] == "sepia"
    assert ui["sidebarW"] == 320
    assert ui["viewerWrap"] is True


def test_unknown_theme_falls_back_to_dark(client):
    data = client.put("/api/agent-zero/app/theme", json={
        "theme": "not-a-theme", "themeSchedule": {"mode": "manual"},
    }).json()["data"]
    assert data["theme"] == "dark"
