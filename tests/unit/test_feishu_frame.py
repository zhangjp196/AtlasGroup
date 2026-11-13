from __future__ import annotations

import pytest

from services.agent_zero.services.feishu_frame import Frame


def test_frame_round_trip():
    frame = Frame(SeqID=7, LogID=9, service=42, method=1)
    frame.add_header("type", "event")
    frame.add_header("sum", "1")
    frame.payload = b'{"hello": "\xe4\xb8\x96\xe7\x95\x8c"}'

    parsed = Frame.FromString(frame.SerializeToString())
    assert (parsed.SeqID, parsed.LogID, parsed.service, parsed.method) == (7, 9, 42, 1)
    assert [(h.key, h.value) for h in parsed.headers] == [("type", "event"), ("sum", "1")]
    assert parsed.payload == frame.payload


def test_ping_frame_matches_lark():
    lark = pytest.importorskip("lark_oapi.ws.pb.pbbp2_pb2")

    ping = Frame(service=123)
    ping.add_header("type", "ping")
    reference = lark.Frame.FromString(ping.SerializeToString())
    assert reference.service == 123
    assert [(h.key, h.value) for h in reference.headers] == [("type", "ping")]

    original = lark.Frame(SeqID=1, LogID=2, service=3, method=4)
    original.headers.add(key="type", value="event")
    original.headers.add(key="sum", value="2")
    original.payload = b"payload"
    parsed = Frame.FromString(original.SerializeToString())
    assert (parsed.SeqID, parsed.LogID, parsed.service, parsed.method) == (1, 2, 3, 4)
    assert [(h.key, h.value) for h in parsed.headers] == [("type", "event"), ("sum", "2")]
    assert parsed.payload == b"payload"
