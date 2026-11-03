"""文件检查点：快照 / 差异 / 回退。"""
from __future__ import annotations

import pytest

from services.agent_zero.services import file_checkpoint_service as svc
from services.agent_zero.services import workspace_service


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "node_modules").mkdir()
    (root / "src" / "app.py").write_text("print('one')\n", encoding="utf-8")
    (root / "keep.txt").write_text("keep\n", encoding="utf-8")
    (root / ".git" / "config").write_text("secret\n", encoding="utf-8")
    (root / "node_modules" / "x.js").write_text("module\n", encoding="utf-8")

    monkeypatch.setattr(svc, "storage_root", lambda: tmp_path / "checkpoints")
    monkeypatch.setattr(workspace_service, "get_workspace_root",
                        lambda kind, wid: root)
    monkeypatch.setattr(workspace_service, "has_working_dir", lambda wid: True)
    return root


def _snapshot(ws, message_id="msg1", label="hello"):
    return svc.snapshot("ws1", conversation_id="conv1",
                        message_id=message_id, run_id="run1", label=label)


def test_snapshot_skips_vcs_and_dependencies(ws):
    cp = _snapshot(ws)
    paths = {e["path"] for e in cp["files"]}
    assert paths == {"src/app.py", "keep.txt"}
    assert cp["complete"] is True
    assert (svc.storage_root() / "ws1" / "blobs").is_dir()


def test_diff_and_restore_roundtrip(ws):
    cp = _snapshot(ws)
    (ws / "src" / "app.py").write_text("print('two')\n", encoding="utf-8")
    (ws / "keep.txt").unlink()
    (ws / "new.txt").write_text("new\n", encoding="utf-8")

    data = svc.diff("ws1", cp["id"])
    assert data["available"] is True
    assert data["counts"] == {"added": 1, "modified": 1, "deleted": 1}
    assert [x["path"] for x in data["modified"]] == ["src/app.py"]
    assert [x["path"] for x in data["deleted"]] == ["keep.txt"]

    result = svc.restore("ws1", cp["id"])
    assert result["restored"] == 2
    assert result["deleted"] == 1
    assert (ws / "src" / "app.py").read_text(encoding="utf-8") == "print('one')\n"
    assert (ws / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert not (ws / "new.txt").exists()
    # 忽略目录原样保留
    assert (ws / ".git" / "config").exists()
    assert (ws / "node_modules" / "x.js").exists()

    assert svc.diff("ws1", cp["id"])["counts"] == {
        "added": 0, "modified": 0, "deleted": 0}


def test_large_file_skipped_but_never_deleted(ws, monkeypatch):
    monkeypatch.setattr(svc, "MAX_FILE_BYTES", 16)
    (ws / "big.bin").write_bytes(b"x" * 30)
    cp = _snapshot(ws)
    assert all(e["path"] != "big.bin" for e in cp["files"])
    assert cp["skipped_large"] == 1
    # 新增一个小于上限的文件仍会在回退时删除；大文件不受影响
    (ws / "small.txt").write_text("s\n", encoding="utf-8")
    result = svc.restore("ws1", cp["id"])
    assert result["deleted"] == 1
    assert (ws / "big.bin").exists()


def test_incomplete_snapshot_does_not_delete_added(ws, monkeypatch):
    monkeypatch.setattr(svc, "MAX_FILES", 1)
    cp = _snapshot(ws)
    assert cp["complete"] is False
    (ws / "later.txt").write_text("later\n", encoding="utf-8")
    result = svc.restore("ws1", cp["id"])
    assert result["deleted"] == 0
    assert result["delete_skipped"] is True
    assert (ws / "later.txt").exists()


def test_same_message_replaces_previous_checkpoint(ws):
    first = _snapshot(ws)
    (ws / "keep.txt").write_text("changed\n", encoding="utf-8")
    second = _snapshot(ws)
    items = svc.list_for_conversation("conv1", "ws1")
    assert [c["id"] for c in items] == [second["id"]]
    svc.restore("ws1", second["id"])
    assert (ws / "keep.txt").read_text(encoding="utf-8") == "changed\n"
    assert first["id"] != second["id"]


def test_restore_is_refused_when_workspace_root_changed(ws, tmp_path, monkeypatch):
    cp = _snapshot(ws)
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr(workspace_service, "get_workspace_root",
                        lambda kind, wid: other)
    with pytest.raises(ValueError):
        svc.restore("ws1", cp["id"])
