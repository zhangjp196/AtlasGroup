from services.agent_zero.task_layer.tool_observability import (
    _args_previewable,
    _args_skeleton,
    _result_is_error,
)


def test_tool_args_are_previewed_only_when_short_and_redacted_otherwise():
    short = {"path": "src/main.py", "line": 12}
    long = {"content": "secret body " * 100}

    assert _args_previewable(short)
    assert not _args_previewable(long)
    assert _args_skeleton(long) == {"content": "<str len=1200>"}


def test_tool_error_summary_uses_error_envelope():
    assert _result_is_error('{"error":"permission denied"}')
    assert not _result_is_error('{"output":"ok"}')
    assert not _result_is_error("not json")
