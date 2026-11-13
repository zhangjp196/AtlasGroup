from __future__ import annotations

import asyncio


from pydantic_ai.exceptions import ModelHTTPError

from services.agent_zero.runtime.zero import llm


def test_chat_messages_convert_roundtrip():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "FILE"},
    ]
    out = llm._to_model_messages(messages)
    assert len(out) == 4
    assert out[0].parts[0].part_kind == "system-prompt"
    assert out[1].parts[0].part_kind == "user-prompt"
    assert out[2].parts[0].part_kind == "tool-call"
    assert out[2].parts[0].tool_name == "read_file"
    assert out[3].parts[0].part_kind == "tool-return"
    # tool 结果消息本身没有工具名，必须由前置 assistant 回填
    assert out[3].parts[0].tool_name == "read_file"


def test_tool_definitions_and_args_parsing():
    defs = llm._tool_definitions([
        {"type": "function", "function": {
            "name": "read_file", "description": "read",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
        {"name": "no_schema"},
        {"type": "function", "function": {"description": "missing name"}},
    ])
    assert [d.name for d in defs] == ["read_file", "no_schema"]
    assert llm._args_dict('{"a": 1}') == {"a": 1}
    assert llm._args_dict("not-json") == {}
    assert llm._args_dict([1, 2]) == {"_raw": [1, 2]}


def test_usage_and_finish_mapping():
    assert llm._finish_reason("tool_call") == "tool_calls"
    assert llm._finish_reason("length") == "length"

    class U:
        input_tokens = 12
        output_tokens = 3
        cache_read_tokens = 5

    assert llm._usage_pair(U()) == {"input_tokens": 12, "output_tokens": 3}
    ev = llm._usage_event(U())
    assert ev["prompt_tokens"] == 12 and ev["prompt_tokens_details"]["cached_tokens"] == 5
    assert llm._usage_event(None) == {}


def test_protocol_aliases_and_anthropic_base_urls():
    for value in ("anthropic", "Anthropic Messages", "claude", "messages"):
        assert llm._normalize_protocol(value) == "anthropic"
    assert llm._normalize_protocol("responses") == "openai-response"
    assert llm._normalize_protocol("whatever") == "openai-chat"

    assert llm._normalize_anthropic_base_url("https://api.anthropic.com") == "https://api.anthropic.com"
    assert llm._normalize_anthropic_base_url("https://api.anthropic.com/v1") == "https://api.anthropic.com"
    assert (llm._normalize_anthropic_base_url("https://api.anthropic.com/v1/messages")
            == "https://api.anthropic.com")


def test_subscription_selects_anthropic_endpoint(monkeypatch):
    monkeypatch.setattr(llm, "_llm_section", lambda: {
        "base_url": "https://opencode.ai/inference/openai/v1",
        "api_key": "sub-key",
        "model": "gpt-x",
        "protocol": "openai-chat",
        "subscriptions": {
            "sub": {
                "name": "OpenCode Zen",
                "base_url": "https://opencode.ai/inference/openai/v1",
                "api_key": "sub-key",
            },
        },
        "models": {
            "claude": {
                "model": "claude-sonnet-4-5",
                "protocol": "anthropic",
                "subscription_id": "sub",
            },
        },
    })
    cfg = llm._resolve_llm_config("claude", use_active=False)
    assert cfg["protocol"] == "anthropic"
    assert cfg["base_url"] == "https://opencode.ai/inference/anthropic"
    assert cfg["api_key"] == "sub-key"


def test_consecutive_tool_results_are_grouped():
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "a", "arguments": "{}"}},
            {"id": "c2", "function": {"name": "b", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "one"},
        {"role": "tool", "tool_call_id": "c2", "content": "two"},
    ]
    out = llm._to_model_messages(messages)
    assert len(out) == 2
    assert [p.tool_call_id for p in out[-1].parts] == ["c1", "c2"]


def test_model_settings_fall_back_to_profile_max_tokens(monkeypatch):
    """调用方传 0（不限）时用档案 max_tokens，别让端点默认 2048 截断思考。"""
    monkeypatch.setattr(llm, "_llm_section", lambda: {})
    monkeypatch.setattr(llm, "_request_timeout_setting", lambda *a, **k: None)
    monkeypatch.setattr(llm, "get_reasoning_effort", lambda: "")

    settings = llm._model_settings({"max_tokens": 4096}, 0.2, 0, 300, False)
    assert settings["max_tokens"] == 4096

    settings = llm._model_settings({"max_tokens": 4096}, 0.2, 2048, 300, False)
    assert settings["max_tokens"] == 2048

    settings = llm._model_settings({"max_tokens": 0}, 0.2, 0, 300, False)
    assert "max_tokens" not in settings


def test_anthropic_model_settings_use_thinking_and_skip_temperature(monkeypatch):
    monkeypatch.setattr(llm, "_llm_section", lambda: {})
    monkeypatch.setattr(llm, "_request_timeout_setting", lambda *a, **k: None)
    monkeypatch.setattr(llm, "get_reasoning_effort", lambda: "medium")
    settings = llm._model_settings(
        {"protocol": "anthropic", "base_url": "https://api.anthropic.com"}, 0.2, 1024, 300, False)
    assert settings["thinking"] == "medium"
    assert settings["max_tokens"] == 11024
    assert "temperature" not in settings
    assert "openai_reasoning_effort" not in settings

    monkeypatch.setattr(llm, "get_reasoning_effort", lambda: "custom-level")
    settings = llm._model_settings(
        {"protocol": "anthropic", "base_url": "https://api.anthropic.com"}, 0.2, 1024, 300, False)
    assert "thinking" not in settings
    assert "temperature" not in settings


def test_opencode_anthropic_models_are_supported():
    from services.agent_zero.routes import app_routes

    protocol, supported, _note = app_routes._opencode_protocol(
        "claude-sonnet-4-5", "opencode-zen")
    assert (protocol, supported) == ("anthropic", True)
    protocol, supported, _note = app_routes._opencode_protocol(
        "minimax-m3", "opencode-go")
    assert (protocol, supported) == ("anthropic", True)
    protocol, supported, note = app_routes._opencode_protocol(
        "gemini-3-pro", "opencode-zen")
    assert protocol == "google" and supported is False and note


def test_error_kinds_and_retry_after():
    e429 = ModelHTTPError(429, "m", {"error": "slow"}, headers={"Retry-After": "2"})
    err = llm._request_error(e429, "code", "http://gw")
    assert err.kind == "rate_limit" and err.retry_after == 2.0

    e400 = ModelHTTPError(400, "m", {"error": "maximum context length exceeded"})
    assert llm._request_error(e400, "code", "http://gw").kind == "overflow"

    assert llm._request_error(ModelHTTPError(500, "m", {}), "c", "u").kind == "upstream"
    assert llm._request_error(ModelHTTPError(401, "m", {}), "c", "u").kind == "auth"
    assert llm._is_retryable(llm.LlmError("x", None, "network"))
    assert not llm._is_retryable(llm.LlmError("x", 400, "overflow"))


def test_retry_policy_defaults_and_clamps(monkeypatch):
    monkeypatch.setattr(llm, "_llm_section", lambda: {})
    # 默认 20 次 × 2.5s：网络抖动靠自动重试吃掉，不再几次就报错。
    assert llm._retry_policy() == (20, 2.5)

    monkeypatch.setattr(llm, "_llm_section", lambda: {"retry_attempts": 99, "retry_backoff_s": 999})
    attempts, backoff = llm._retry_policy()
    assert attempts == 50 and backoff == 30.0


def test_retry_wait_is_fixed_interval(monkeypatch):
    """重试间隔固定（不再指数退避）；429 的 Retry-After 优先。"""
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    err = llm.LlmError("x", None, "network")

    async def run():
        for attempt in range(4):
            await llm._retry_wait(err, attempt, 1.0)

    asyncio.run(run())
    assert slept == [1.0, 1.0, 1.0, 1.0]

    slept.clear()
    err429 = llm.LlmError("x", 429, "rate_limit", retry_after=5.0)
    asyncio.run(llm._retry_wait(err429, 0, 1.0))
    assert slept == [5.0]


def test_stream_watchdog_defaults_disabled(monkeypatch):
    """空闲中止默认关闭（0 = 不中止），心跳默认 20s。"""
    monkeypatch.setattr(llm, "_llm_section", lambda: {})
    assert llm.get_stream_watchdog_config() == (20.0, 0.0)
    assert llm.get_run_stall_timeout() == 0.0

    monkeypatch.setattr(llm, "_llm_section", lambda: {
        "stream_keepalive_s": 5, "stream_idle_timeout_s": 600, "run_event_stall_s": 900})
    assert llm.get_stream_watchdog_config() == (5.0, 600.0)
    assert llm.get_run_stall_timeout() == 900.0


def test_resume_split_strips_repeated_prefix():
    """断流续写：重复前缀被切掉，未对齐时先不投递。"""
    assert llm._resume_split("", "hello") == ("hello", True)
    # 模型从头重放（还没放完已发内容）→ 先不投递
    assert llm._resume_split("hello world", "hello") == ("", False)
    # 重放完毕并继续 → 只投递新增部分
    assert llm._resume_split("hello world", "hello world! more") == ("! more", True)
    # 模型直接接着写（没有重叠）→ 原样投递
    assert llm._resume_split("hello world", "continue here") == ("continue here", True)
    # 后缀重叠（例如只重复了半句）→ 切掉重叠
    assert llm._resume_split("abc def", "def ghi") == (" ghi", True)


def test_stream_full_resumes_after_mid_stream_break(monkeypatch):
    """已输出内容后断流 → 自动续写重试，且不重复给用户看已发文本。"""
    calls: list[list[dict]] = []

    async def fake_once(code, messages, tools=None, temperature=0.2,
                        max_tokens=None, timeout=300.0):
        calls.append(messages)
        if len(calls) == 1:
            yield {"type": "delta", "content": "第一段"}
            raise llm.LlmError("connection reset", None, "network")
        # 第二次：模型重放了第一段，然后继续
        yield {"type": "delta", "content": "第一段"}
        yield {"type": "delta", "content": "第二段"}
        yield {"type": "done", "content": "第一段第二段", "reasoning": "",
               "tool_calls": [], "usage": {}, "finish_reason": "stop"}

    async def fake_overflow():
        return 0

    async def no_wait(err, attempt, base):
        return None

    monkeypatch.setattr(llm, "_llm_chat_stream_full_once", fake_once)
    monkeypatch.setattr(llm, "get_overflow_recovery_attempts", fake_overflow)
    monkeypatch.setattr(llm, "_retry_wait", no_wait)
    monkeypatch.setattr(llm, "_llm_section", lambda: {})

    async def run():
        out = []
        async for ev in llm.llm_chat_stream_full("default", [{"role": "user", "content": "hi"}]):
            out.append(ev)
        return out

    events = asyncio.run(run())
    deltas = [e["content"] for e in events if e.get("type") == "delta"]
    assert deltas == ["第一段", "第二段"], deltas
    # 第二次调用带着「已发内容 + 续写提示」
    assert calls[1][-1]["role"] == "user" and "从中断处继续" in calls[1][-1]["content"]
    assert calls[1][-2] == {"role": "assistant", "content": "第一段"}
    done = [e for e in events if e.get("type") == "done"][0]
    assert done["content"] == "第一段第二段"


def test_stream_full_retries_before_first_token(monkeypatch):
    """首个 token 之前的网络失败：整轮重放重试，不丢内容。"""
    calls = {"n": 0}

    async def fake_once(code, messages, tools=None, temperature=0.2,
                        max_tokens=None, timeout=300.0):
        calls["n"] += 1
        if calls["n"] < 3:
            raise llm.LlmError("boom", None, "network")
            yield  # pragma: no cover
        yield {"type": "delta", "content": "ok"}
        yield {"type": "done", "content": "ok", "reasoning": "", "tool_calls": [],
               "usage": {}, "finish_reason": "stop"}

    async def fake_overflow():
        return 0

    async def no_wait(err, attempt, base):
        return None

    monkeypatch.setattr(llm, "_llm_chat_stream_full_once", fake_once)
    monkeypatch.setattr(llm, "get_overflow_recovery_attempts", fake_overflow)
    monkeypatch.setattr(llm, "_retry_wait", no_wait)
    monkeypatch.setattr(llm, "_llm_section", lambda: {})

    async def run():
        return [ev async for ev in llm.llm_chat_stream_full("default", [])]

    events = asyncio.run(run())
    assert calls["n"] == 3
    assert [e["content"] for e in events if e.get("type") == "delta"] == ["ok"]
