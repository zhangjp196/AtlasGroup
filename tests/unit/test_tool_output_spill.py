"""超大工具输出落盘：上下文只留预览 + 路径，避免一条结果顶爆窗口。"""
from __future__ import annotations

import json
import os

from services.agent_zero.runtime.zero import dispatch


def _loop():
    return dispatch.ToolDispatcher()


def _task():
    return {"id": "t1", "workspace_id": "ws", "camp": "default", "submitter": "u"}


def test_oversized_output_is_spilled(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch.workspace_service, "exec_root",
                        lambda wid: str(tmp_path))
    monkeypatch.setattr(dispatch.ToolDispatcher, "_spill_limit",
                        staticmethod(lambda: 1000))

    big = {"ok": True, "stdout": "x" * 5000,
           "display": {"type": "file", "path": "a.py"}}
    out = _loop()._spill_if_huge(big, _task(), "shell")

    assert out["truncated"] is True
    assert out["ok"] is True
    assert out["saved_to"].startswith(".atlas/tool-output/")
    assert out["display"] == {"type": "file", "path": "a.py"}   # UI 预览不丢
    assert len(out["preview"]) == 4000
    assert out["chars"] == len(json.dumps(big, ensure_ascii=False))
    saved = tmp_path / out["saved_to"]
    assert saved.exists()
    assert "x" * 5000 in saved.read_text(encoding="utf-8")
    assert "fs 的 read" in out["note"]


def test_small_output_is_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch.workspace_service, "exec_root",
                        lambda wid: str(tmp_path))
    monkeypatch.setattr(dispatch.ToolDispatcher, "_spill_limit",
                        staticmethod(lambda: 10_000))
    small = {"ok": True, "stdout": "hello"}
    assert _loop()._spill_if_huge(small, _task(), "shell") is small


def test_spill_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch.workspace_service, "exec_root",
                        lambda wid: str(tmp_path))
    monkeypatch.setattr(dispatch.ToolDispatcher, "_spill_limit",
                        staticmethod(lambda: 0))
    big = {"stdout": "y" * 5000}
    assert _loop()._spill_if_huge(big, _task(), "shell") is big


def test_spill_without_workspace_returns_original(monkeypatch):
    monkeypatch.setattr(dispatch.workspace_service, "exec_root",
                        lambda wid: "")
    monkeypatch.setattr(dispatch.ToolDispatcher, "_spill_limit",
                        staticmethod(lambda: 10))
    big = {"stdout": "z" * 5000}
    assert _loop()._spill_if_huge(big, _task(), "shell") is big


def test_spill_prunes_old_files(tmp_path):
    folder = tmp_path / "spills"
    folder.mkdir()
    for i in range(55):
        path = folder / f"f{i}.txt"
        path.write_text("x", encoding="utf-8")
        os.utime(path, (1_700_000_000 + i, 1_700_000_000 + i))
    dispatch.ToolDispatcher._prune_spills(str(folder), keep=50)
    left = sorted(p.name for p in folder.iterdir())
    assert len(left) == 50
    assert "f0.txt" not in left          # 最旧的被清掉
    assert "f54.txt" in left
