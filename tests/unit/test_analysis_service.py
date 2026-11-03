from __future__ import annotations

from services.agent_zero.services import analysis_service


def test_default_analysis_path_is_dated_and_typed():
    rel = analysis_service.default_relpath("通道状态 分析", "report", "md")
    assert rel.startswith(".atlas/reports/")
    assert rel.endswith(".md")
    # .atlas/<kind>/<YYYY-MM-DD>/<HHMM>-<slug>.<ext>
    parts = rel.split("/")
    assert len(parts) == 4
    assert len(parts[2]) == 10 and parts[2].count("-") == 2
    assert parts[3].endswith(".md")


def test_kind_and_ext_are_normalized():
    rel = analysis_service.default_relpath("x", "unknown-kind!", "exe")
    assert rel.startswith(".atlas/unknown-kind/")
    assert rel.endswith(".md")
    assert analysis_service.default_relpath("x", "analysis", "txt").endswith(".txt")


def test_explicit_path_is_respected(monkeypatch):
    captured = {}

    class FakeWs:
        @staticmethod
        def write_file(ws_id, rel, content):
            captured.update(ws_id=ws_id, rel=rel, content=content)
            return {"path": rel, "size": len(content)}

    monkeypatch.setattr(analysis_service, "workspace_service", FakeWs)
    info = analysis_service.save("ws1", title="t", content="body", path="docs/report.md")
    assert captured["rel"] == "docs/report.md"
    assert captured["content"] == "body"
    assert info["path"] == "docs/report.md"
