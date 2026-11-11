"""Magpie 本机脱敏：占位、可逆还原、流式切分与 API。"""
from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace

from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta

from services.agent_zero.runtime.zero import llm
from services.agent_zero.services import magpie_service

TEXT = (
    "联系 alice@example.com 或 13800138000，身份证 110101199003071234，"
    "IP 10.0.0.8。\n"
    "密钥 sk-abcdefghijklmnopqrstuvwxyz123456，"
    "Authorization: Bearer abcdefghijklmnopqrstuv.\n"
    "密码 password=SuperSecret123\n"
    "路径 /Users/zhangjp/Documents/secret.txt\n"
)


def test_redact_and_restore_roundtrip():
    redactor = magpie_service.Redactor()
    masked = redactor.redact_text(TEXT)
    for raw in ("alice@example.com", "13800138000", "110101199003071234",
                "10.0.0.8", "sk-abcdefghijklmnopqrstuvwxyz123456",
                "SuperSecret123", "/Users/zhangjp/Documents/secret.txt"):
        assert raw not in masked, raw
    assert "⟦MG_" in masked
    assert redactor.count >= 7
    assert redactor.restore_text(masked) == TEXT


def test_categories_can_be_disabled():
    settings = dict(magpie_service.DEFAULT_SETTINGS)
    settings["pii"] = False
    redactor = magpie_service.Redactor(settings)
    masked = redactor.redact_text("mail alice@example.com")
    assert "alice@example.com" in masked
    assert not redactor.by_kind.get("EMAIL")


def test_disabled_returns_original():
    redactor = magpie_service.Redactor({**magpie_service.DEFAULT_SETTINGS,
                                        "enabled": False})
    assert redactor.redact_text(TEXT) == TEXT
    assert redactor.count == 0


def test_messages_add_note_and_restore_tool_calls():
    messages = [
        {"role": "system", "content": "工作区 /Users/alice/project"},
        {"role": "user", "content": "把结果发到 alice@example.com"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call-1", "type": "function", "function": {
                "name": "read_file",
                "arguments": json.dumps({"path": "/Users/alice/project/a.txt"}),
            }},
        ]},
    ]
    masked, redactor = magpie_service.redact_messages(messages)
    dumped = json.dumps(masked, ensure_ascii=False)
    assert "/Users/alice" not in dumped
    assert "alice@example.com" not in dumped
    assert magpie_service.NOTE in masked[0]["content"]
    restored = redactor.restore_obj(masked[2]["tool_calls"])
    assert restored[0]["function"]["arguments"] == json.dumps(
        {"path": "/Users/alice/project/a.txt"})


def test_stream_restorer_handles_split_placeholder():
    redactor = magpie_service.Redactor()
    original = "邮箱 alice@example.com，路径 /Users/alice/a.txt 结束"
    masked = redactor.redact_text(original)
    restorer = redactor.stream()
    out = "".join(restorer.feed(ch) for ch in masked) + restorer.flush()
    assert out == original


def test_api_settings_and_preview(client):
    api = "/api/agent-zero/app/magpie"
    status = client.get(api).json()["data"]
    assert status["settings"]["enabled"] is True
    assert status["categories"]

    preview = client.post(f"{api}/test", json={
        "text": "mail alice@example.com, key sk-abcdefghijklmnopqrstuvwxyz123456",
    }).json()["data"]
    assert preview["redacted"] >= 2
    assert "alice@example.com" not in preview["masked"]
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in preview["masked"]

    off = client.put(f"{api}/settings", json={"enabled": False}).json()["data"]
    assert off["settings"]["enabled"] is False
    unchanged = client.post(f"{api}/test", json={
        "text": "mail alice@example.com"}).json()["data"]
    assert unchanged["redacted"] == 0
    assert unchanged["masked"] == "mail alice@example.com"
    client.put(f"{api}/settings", json={"enabled": True})


def test_llm_call_masks_prompt_and_restores_reply(monkeypatch):
    captured: dict[str, str] = {}

    class FakeModel:
        async def request(self, messages, settings, params):
            text = " ".join(
                str(getattr(part, "content", ""))
                for message in messages for part in getattr(message, "parts", [])
            )
            captured["prompt"] = text
            match = re.search(r"⟦MG_EMAIL_\d+⟧", text)
            assert match, text
            captured["token"] = match.group(0)
            return SimpleNamespace(
                parts=[TextPart(content=f"请联系 {match.group(0)}")],
                usage=None, model_name="fake",
            )

    @asynccontextmanager
    async def fake_open(cfg):
        yield FakeModel()

    async def fake_config(code):
        return {"code": "m", "concurrency": 2, "model": "x",
                "base_url": "https://x/v1"}

    monkeypatch.setattr(llm, "_open_model", fake_open)
    monkeypatch.setattr(llm, "get_llm_config", fake_config)
    monkeypatch.setattr(llm, "_model_settings", lambda *a, **k: {})

    result = asyncio.run(llm._llm_chat_once(
        "m", [{"role": "user", "content": "发到 alice@example.com"}]))

    assert "alice@example.com" not in captured["prompt"]
    assert captured["token"] in captured["prompt"]
    assert result.content == "请联系 alice@example.com"


def test_stream_masks_and_restores_split_placeholder(monkeypatch):
    captured: dict[str, str] = {}
    events: list = []

    class FakeStream:
        usage = None
        finish_reason = "stop"

        def __init__(self):
            self.index = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.index >= len(events):
                raise StopAsyncIteration
            event = events[self.index]
            self.index += 1
            return event

        def get(self):
            return SimpleNamespace(parts=[])

    class FakeModel:
        def request_stream(self, messages, settings, params):
            text = " ".join(
                str(getattr(part, "content", ""))
                for message in messages for part in getattr(message, "parts", [])
            )
            captured["prompt"] = text
            token = re.search(r"⟦MG_EMAIL_\d+⟧", text).group(0)
            captured["token"] = token
            events[:] = [
                PartStartEvent(index=0, part=TextPart(content=f"请联系 {token[:5]}")),
                PartDeltaEvent(index=0,
                               delta=TextPartDelta(content_delta=f"{token[5:]} 结束")),
            ]
            return FakeStream()

    @asynccontextmanager
    async def fake_open(cfg):
        yield FakeModel()

    async def fake_config(code):
        return {"code": "m", "concurrency": 2, "model": "x",
                "base_url": "https://x/v1"}

    monkeypatch.setattr(llm, "_open_model", fake_open)
    monkeypatch.setattr(llm, "get_llm_config", fake_config)
    monkeypatch.setattr(llm, "_model_settings", lambda *a, **k: {})

    async def run():
        out = []
        async for event in llm._llm_chat_stream_full_once(
            "m", [{"role": "user", "content": "发到 alice@example.com"}]
        ):
            out.append(event)
        return out

    out = asyncio.run(run())
    assert "alice@example.com" not in captured["prompt"]
    deltas = "".join(str(e.get("content") or "") for e in out if e.get("type") == "delta")
    done = next(e for e in out if e.get("type") == "done")
    assert deltas == "请联系 alice@example.com 结束"
    assert done["content"] == "请联系 alice@example.com 结束"
