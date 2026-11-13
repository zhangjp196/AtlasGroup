"""DeepSeek DSML 文本工具调用：还原成结构化调用并从正文里过滤。"""

from __future__ import annotations

from services.agent_zero.runtime.zero.dsml import (
    DsmlStreamFilter,
    clean_dsml_text,
    extract_dsml_calls,
)

CANONICAL = (
    "<｜DSML｜tool_calls>\n"
    '<｜DSML｜invoke name="session_manage">\n'
    '<｜DSML｜parameter name="action" string="true">clear</｜DSML｜parameter>\n'
    "</｜DSML｜invoke>\n"
    "</｜DSML｜tool_calls>"
)


def test_extract_fullwidth_dsml_call():
    calls = extract_dsml_calls("好的，我来清空。\n" + CANONICAL)
    assert [c["name"] for c in calls] == ["session_manage"]
    assert calls[0]["arguments"] == {"action": "clear"}
    assert calls[0]["id"].startswith("dsml_")


def test_extract_ascii_and_multiple_calls_with_json_values():
    text = (
        "<|DSML|tool_calls>"
        '<|DSML|invoke name="write_file">'
        '<|DSML|parameter name="path" string="true">a.txt</|DSML|parameter>'
        '<|DSML|parameter name="size" string="false">12</|DSML|parameter>'
        "</|DSML|invoke>"
        '<|DSML|invoke name="shell">'
        '<|DSML|parameter name="command" string="true">ls</|DSML|parameter>'
        "</|DSML|invoke>"
        "</|DSML|tool_calls>"
    )
    calls = extract_dsml_calls(text)
    assert [(c["name"], c["arguments"]) for c in calls] == [
        ("write_file", {"path": "a.txt", "size": 12}),
        ("shell", {"command": "ls"}),
    ]


def test_extract_degraded_double_pipe_variant():
    text = (
        "<||DSML||function_calls>"
        '<||DSML||invoke name="session_manage">'
        '<||DSML||parameter name="action" string="true">clear</||DSML||parameter>'
        "</||DSML||invoke>"
        "</||DSML||function_calls>"
    )
    calls = extract_dsml_calls(text)
    assert calls[0]["name"] == "session_manage"
    assert calls[0]["arguments"] == {"action": "clear"}


def test_extract_bare_invoke_and_json_body():
    text = (
        '<｜DSML｜invoke name="ask_user">{"question": "继续吗？"}</｜DSML｜invoke>'
    )
    calls = extract_dsml_calls(text)
    assert calls == [{**calls[0], "name": "ask_user", "arguments": {"question": "继续吗？"}}]


def test_clean_dsml_text_keeps_surrounding_answer():
    text = "我先执行。\n" + CANONICAL + "\n已处理。"
    assert clean_dsml_text(text) == "我先执行。\n\n已处理。"
    assert "DSML" not in clean_dsml_text(text)


def test_stream_filter_hides_markup_split_into_characters():
    text = "先说一句。\n" + CANONICAL + "\n结束了。"
    stream = DsmlStreamFilter()
    parts = []
    for char in text:
        parts.append(stream.feed(char))
    parts.append(stream.finish())
    out = "".join(parts)
    assert "DSML" not in out
    assert out == "先说一句。\n\n结束了。"


def test_stream_filter_keeps_normal_angle_brackets():
    stream = DsmlStreamFilter()
    out = stream.feed("普通 HTML：<div>hi</div> 和 a < b")
    out += stream.finish()
    assert out == "普通 HTML：<div>hi</div> 和 a < b"


def test_stream_filter_drops_unclosed_dsml_block():
    stream = DsmlStreamFilter()
    out = stream.feed("答：" + CANONICAL.split("</｜DSML｜invoke>")[0])
    out += stream.finish()
    assert out == "答："


def test_stream_filter_handles_plain_calls_tag_and_tail():
    """退化输出会把外壳写成 <｜DSML｜calls>；外壳后紧跟的正常文字不能被吞。"""
    text = (
        "< |DSML| calls>\n"
        '< |DSML| invoke name="session_manage">\n'
        '< |DSML| parameter name="action" string="true">clear</ |DSML| parameter>\n'
        "</ |DSML| invoke>\n"
        "</ |DSML| calls>"
    )
    stream = DsmlStreamFilter()
    out = stream.feed("x" + text + "y") + stream.finish()
    assert out == "xy"
    calls = extract_dsml_calls(text)
    assert calls[0]["name"] == "session_manage"
    assert calls[0]["arguments"] == {"action": "clear"}


def test_dsml_calls_only_kept_when_tool_is_available():
    from types import SimpleNamespace

    from services.agent_zero.runtime.zero.llm import _dsml_calls_of

    text = (
        CANONICAL
        + '<｜DSML｜invoke name="launch_missiles">'
        '<｜DSML｜parameter name="target" string="true">x</｜DSML｜parameter>'
        "</｜DSML｜invoke>"
    )
    allowed = [SimpleNamespace(name="session_manage")]
    calls = _dsml_calls_of(text, allowed)
    assert [call["name"] for call in calls] == ["session_manage"]


def test_dsml_session_manage_runs_even_without_tool_list():
    """没有下发工具清单时，会话指令仍要被识别（否则只剩一串 DSML 原文）。"""
    from services.agent_zero.runtime.zero.llm import _dsml_calls_of

    calls = _dsml_calls_of(CANONICAL, [])
    assert [call["name"] for call in calls] == ["session_manage"]
    assert calls[0]["arguments"] == {"action": "clear"}
