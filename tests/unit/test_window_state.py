"""桌面窗口状态校验：历史 window.json 的屏幕外坐标不能再让 app 闪退。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "atlasx_launcher", Path(__file__).resolve().parents[2] / "desktop" / "launcher.py")
launcher = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(launcher)


def test_offscreen_state_is_clamped(monkeypatch):
    monkeypatch.setattr(launcher, "_screen_visible_frame", lambda: (0, 0, 1728, 1117))
    out = launcher._safe_window_state(
        {"width": 1920, "height": 1050, "x": 1728, "y": -5})
    assert out["width"] == 1728            # 不宽于屏幕
    assert out["x"] == 0                   # 夹回可见区域
    assert out["y"] == 0


def test_missing_position_stays_centered(monkeypatch):
    monkeypatch.setattr(launcher, "_screen_visible_frame", lambda: (0, 0, 1440, 900))
    out = launcher._safe_window_state({"width": 1200, "height": 800})
    assert out["x"] is None and out["y"] is None


def test_no_screen_info_keeps_state(monkeypatch):
    monkeypatch.setattr(launcher, "_screen_visible_frame", lambda: None)
    out = launcher._safe_window_state({"width": 1200, "height": 800, "x": 10, "y": 20})
    assert out == {"width": 1200, "height": 800, "x": 10, "y": 20}
