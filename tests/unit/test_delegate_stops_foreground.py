"""派发团队任务后，主持不再在前台重复干活。

用户反馈：「任务依旧分配，为何自己还在前台进行？」——委派只是把任务
建好了，主持却继续 shell / read_file 重复执行同一件事，既烧 token 又与
成员的工作重复。这里守住：`call_team` 成功派发后，下一轮 LLM 调用不再
携带任何工具，只能输出一两句收尾。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

from services.agent_zero.runtime.zero import llm as llm_mod
from services.agent_zero.task_layer import conversation


def _conv() -> Dict[str, Any]:
    return {"id": "conv-test", "model_code": "", "workspace_id": "ws", "team_id": "team1"}


def _role() -> Dict[str, Any]:
    return {"id": "leader", "name": "主持", "envelope": {}, "system_prompt": ""}


def test_delegation_removes_tools_for_wrapup_round(monkeypatch):
    rounds: List[int] = []

    async def fake_stream(self, role, messages, **kw):
        rounds.append(len(kw.get("tools") or []))
        if len(rounds) == 1:
            yield {"type": "done", "content": "", "usage": {},
                   "finish_reason": "tool_calls",
                   "tool_calls": [{
                       "id": "call-delegate",
                       "name": "delegate",
                       "arguments": {
                           "target": "team",
                           "assignments": [{"member_id": "m1", "goal": "做 A"}],
                       },
                   }]}
            return
        yield {"type": "delta", "content": "已派发，等成员完成后继续。"}
        yield {"type": "done", "content": "", "tool_calls": [],
               "usage": {}, "finish_reason": "stop"}

    async def fake_team(conv, user, role, args, depth, call_id=""):
        return json.dumps({
            "status": "dispatched", "task_id": "task-1",
            "member": {"id": "m1", "name": "成员"},
            "run_id": "run-1", "runs": [],
        }, ensure_ascii=False)

    monkeypatch.setattr(llm_mod.ZeroLlm, "chat_stream_full", fake_stream)
    monkeypatch.setattr(conversation, "_exec_call_team", fake_team)
    # 派发本身不需要人工审批，避免测试卡在等待。
    from services.agent_zero.task_layer import permissions

    monkeypatch.setattr(permissions, "decide", lambda *a, **k: {"ask": False})
    monkeypatch.setattr(conversation, "_capture_run_memory", lambda *a, **k: None)

    sink: Dict[str, Any] = {}
    captured_messages: List[List[Dict[str, Any]]] = []

    async def fake_stream_capture(self, role, messages, **kw):
        captured_messages.append(list(messages))
        async for ev in fake_stream(self, role, messages, **kw):
            yield ev

    monkeypatch.setattr(llm_mod.ZeroLlm, "chat_stream_full", fake_stream_capture)

    async def run() -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        async for ev in conversation._stream_role(
                _conv(), "帮我把 A 交给成员", _role(), sink,
                autonomous=False, auto_execute=True):
            out.append(ev)
        return out

    events = asyncio.run(run())

    # 第一轮有工具（至少 delegate），派发后的收尾轮工具被清空。
    assert len(rounds) >= 2, events
    assert rounds[0] > 0
    assert rounds[1] == 0
    # 收尾轮之前注入了「已派发、不要重复执行」的提示。
    wrap = captured_messages[1][-1]
    assert wrap["role"] == "user" and "不要再调用工具重复执行" in wrap["content"]
    # 正文仍能正常落库，不是中途失败。
    assert "已派发" in (sink.get("text") or "")
    assert not sink.get("error")
