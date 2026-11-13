"""全局机器人配置：连接、单会话绑定与聊天路由。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr

from services.agent_zero.routes.local_tools_routes import require_local_request
from services.agent_zero.services.conversation_bots import BotManager
from services.agent_zero.task_layer import conversation

router = APIRouter(prefix="/bots", dependencies=[Depends(require_local_request)])


class BotBody(BaseModel):
    provider: Literal["feishu", "wecom"]
    bot_id: str = Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9_-]+$")
    secret: SecretStr


class BindBody(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=160)
    lane_id: str = Field("", max_length=160)


def _manager(request: Request) -> BotManager:
    return request.app.state.bot_manager


@router.get("")
async def list_bots(request: Request):
    return {"code": 0, "data": {"items": await _manager(request).list()}}


@router.post("")
async def add_bot(request: Request, body: BotBody):
    secret = body.secret.get_secret_value().strip()
    if not secret or len(secret) > 512 or secret.startswith("keychain:"):
        raise HTTPException(status_code=400, detail="请输入有效的机器人 Secret")
    try:
        binding = await _manager(request).add(body.provider, body.bot_id, secret)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"code": 0, "data": binding}


@router.post("/{bot_id}/bind")
async def bind_bot(request: Request, bot_id: str, body: BindBody):
    conv = conversation.get_conversation(body.conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    # 机器人绑定会话根，同时只路由到选中的一个栏目。
    conversation_id = str(conv.get("session_root_id") or conv.get("session_id")
                          or conv.get("id") or body.conversation_id)
    target_lane_id = str(body.lane_id or body.conversation_id)
    lane = conversation.get_conversation(target_lane_id)
    if not lane:
        raise HTTPException(status_code=404, detail="栏目不存在")
    lane_root_id = str(lane.get("session_root_id") or lane.get("session_id") or lane.get("id") or "")
    if lane_root_id != conversation_id:
        raise HTTPException(status_code=400, detail="目标栏目不属于该会话")
    if lane.get("archived"):
        raise HTTPException(status_code=400, detail="不能绑定到已归档栏目")
    try:
        binding = await _manager(request).bind(bot_id, conversation_id, target_lane_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if binding is None:
        raise HTTPException(status_code=404, detail="机器人不存在")
    return {"code": 0, "data": binding}


@router.delete("/{bot_id}/route")
async def unbind_bot(request: Request, bot_id: str):
    if not await _manager(request).unbind(bot_id):
        raise HTTPException(status_code=404, detail="机器人尚未绑定会话")
    return {"code": 0, "data": {"deleted": bot_id}}


@router.delete("/{bot_id}")
async def remove_bot(request: Request, bot_id: str):
    if not await _manager(request).remove(bot_id):
        raise HTTPException(status_code=404, detail="机器人不存在")
    return {"code": 0, "data": {"deleted": bot_id}}
