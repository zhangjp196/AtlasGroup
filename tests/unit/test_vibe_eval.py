"""离线检查 vibe-coding 评测用例；不会调用模型。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from evals.vibe_coding import run as eval_runner
from evals.vibe_coding import run_isolated as isolated_runner
from evals.vibe_coding import summarize as eval_summary
from evals.vibe_coding.run import CASES, _files, _write_seed


def test_eval_cases_have_prompts_and_review_rubrics():
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    assert len(cases) >= 3
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        assert case["prompt"].strip()
        assert case["files"]
        assert len(case["review"]) >= 4


def test_eval_seed_writes_only_workspace_relative_paths(tmp_path):
    _write_seed(tmp_path, {"src/main.py": "print('ok')\n"})
    assert _files(tmp_path) == {"src/main.py": "print('ok')\n"}
    with pytest.raises(ValueError):
        _write_seed(tmp_path, {"../outside.py": "no"})


def test_eval_api_requests_use_agent_zero_prefix(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"code": 0, "data": {"id": "ws-1"}}'

    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(eval_runner, "urlopen", fake_urlopen)
    result = eval_runner.request_json(
        "http://127.0.0.1:10210/", "/workspaces", method="POST",
        body={"name": "test"})

    assert seen["url"] == "http://127.0.0.1:10210/api/agent-zero/workspaces"
    assert seen["timeout"] == 30
    assert result == {"id": "ws-1"}


def test_eval_case_runs_api_lifecycle_and_cleans_up_without_model(tmp_path, monkeypatch):
    """Exercise the runner's workspace → run → collect → cleanup flow offline."""
    case = {
        "id": "offline",
        "title": "offline case",
        "files": {"main.py": "before\n"},
        "prompt": "change it",
        "review": ["check the changed file"],
    }
    root_by_workspace = {}
    calls = []

    def fake_request(_base, path, *, method="GET", body=None):
        calls.append((path, method))
        if path == "/workspaces":
            root_by_workspace["ws-1"] = Path(body["path"])
            return {"id": "ws-1"}
        if path == "/conversations":
            return {"id": "conv-1", "model_code": "test-model"}
        if path == "/conversations/conv-1/messages/run":
            return {"id": "run-1"}
        if path == "/runs/run-1":
            (root_by_workspace["ws-1"] / "main.py").write_text("after\n", encoding="utf-8")
            return {
                "run": {"status": "done"},
                "events": [{"kind": "done", "data": {
                    "text": "完成", "usage": {"total_tokens": 12},
                }}],
            }
        return {}

    monkeypatch.setattr(eval_runner, "request_json", fake_request)
    result = eval_runner.run_case("http://local", case, timeout_s=1)

    assert result["status"] == "done"
    assert result["changed_files"] == ["main.py"]
    assert result["final_text"] == "完成"
    assert result["usage"] == {"total_tokens": 12}
    assert result["score"] == {
        "correctness": None, "verification": None, "scope": None,
        "recovery": None, "communication": None,
    }
    assert calls[-2:] == [
        ("/conversations/conv-1", "DELETE"),
        ("/workspaces/ws-1", "DELETE"),
    ]


def test_eval_cli_writes_machine_readable_report(tmp_path, monkeypatch):
    report_path = tmp_path / "reports" / "baseline.json"
    monkeypatch.setattr(
        eval_runner, "run_case",
        lambda _base, case, _timeout: {"id": case["id"], "status": "done"},
    )
    monkeypatch.setattr("sys.argv", [
        "run.py", "--case", "find-before-edit", "--output", str(report_path),
    ])

    assert eval_runner.main() == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report == [{"id": "find-before-edit", "status": "done"}]


def test_isolated_eval_config_keeps_database_separate_and_key_out_of_file(tmp_path):
    config_path = isolated_runner._eval_config(tmp_path, {
        "base_url": "https://api.example.test/v1",
        "api_key": "never-write-this-key",
        "model": "test-model",
        "protocol": "openai-chat",
    }, 10211)

    config_text = config_path.read_text(encoding="utf-8")
    config = yaml.safe_load(config_text)
    assert "never-write-this-key" not in config_text
    assert config["sqlalchemy"]["db_path"] == "data/agent_zero/agent_zero.db"
    assert config["llm"]["api_key"] == ""
    assert config["llm"]["model"] == "test-model"
    assert config["llm"]["protocol"] == "openai-chat"
    assert "models" not in config["llm"]


@pytest.mark.parametrize("key", [None, "", "EMPTY", "YOUR_API_KEY", "replace_me"])
def test_isolated_eval_rejects_missing_or_placeholder_credentials(key):
    assert isolated_runner._usable_api_key(key) is False


def test_isolated_eval_accepts_configured_credential_without_exposing_it():
    assert isolated_runner._usable_api_key("sk-configured-secret") is True


def test_eval_summary_compares_legacy_and_wrapped_reports(tmp_path):
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps([{
        "id": "case-a", "status": "done", "elapsed_seconds": 12,
        "changed_files": ["a.py"],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        "score": {"correctness": 2, "verification": 1},
    }]), encoding="utf-8")
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({
        "schema_version": 1, "created_at": "2026-11-14T10:00:00Z",
        "model": "gpt-test", "cases": [{
            "id": "case-a", "status": "failed", "error": "timeout",
            "usage": {}, "score": {dimension: None for dimension in (
                "correctness", "verification", "scope", "recovery", "communication",
            )},
        }],
    }), encoding="utf-8")

    report = eval_summary.render([
        eval_summary._load(legacy), eval_summary._load(wrapped),
    ])
    assert "legacy" in report
    assert "gpt-test" in report
    assert "2/5" in report
    assert "7.5" in report
    assert "timeout" in report
    assert "case-a" in report


def test_eval_summary_rejects_scores_outside_rubric():
    with pytest.raises(ValueError, match="必须是 0、1、2"):
        eval_summary.render([{
            "label": "bad", "cases": [{
                "id": "case-a", "score": {"correctness": 3},
            }],
        }])
