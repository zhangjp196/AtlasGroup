"""全局机器人配置：连接、角色 / 群组绑定与外部聊天对话。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr

from services.agent_zero.routes.local_tools_routes import require_local_request
from services.agent_zero.services import team_service
from services.agent_zero.services.conversation_bots import BotManager
from services.agent_zero.stores import zero_store

router = APIRouter(prefix="/bots", dependencies=[Depends(require_local_request)])


class BotBody(BaseModel):
    provider: Literal["feishu", "wecom"]
    bot_id: str = Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9_-]+$")
    secret: SecretStr


class BindBody(BaseModel):
    target_type: Literal["role", "group"]
    target_id: str = Field(min_length=1, max_length=160)


class CreateConversationBody(BaseModel):
    title: str = Field("", max_length=200)


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
    if body.target_type == "role":
        target = zero_store.get_role(body.target_id)
        if (not target or target.get("scope") == "temporary"
                or body.target_id == zero_store.GLOBAL_ROLE_ID):
            raise HTTPException(status_code=404, detail="角色不存在或不可用于机器人")
    else:
        target = team_service.get_team(body.target_id)
        if not target or str(target.get("kind") or "group") != "group":
            raise HTTPException(status_code=404, detail="群组不存在或不可用于机器人")
    try:
        binding = await _manager(request).bind(bot_id, body.target_type, body.target_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if binding is None:
        raise HTTPException(status_code=404, detail="机器人不存在")
    return {"code": 0, "data": binding}


@router.delete("/{bot_id}/route")
async def unbind_bot(request: Request, bot_id: str):
    if not await _manager(request).unbind(bot_id):
        raise HTTPException(status_code=404, detail="机器人不存在")
    return {"code": 0, "data": {"deleted": bot_id}}


@router.get("/{bot_id}/conversations")
async def list_bot_conversations(request: Request, bot_id: str):
    if not any(item["id"] == bot_id for item in await _manager(request).list()):
        raise HTTPException(status_code=404, detail="机器人不存在")
    return {"code": 0, "data": {"items": await _manager(request).conversations(bot_id)}}


@router.post("/{bot_id}/conversations")
async def create_bot_conversation(request: Request, bot_id: str, body: CreateConversationBody):
    try:
        conv = await _manager(request).create_conversation(bot_id, body.title)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if conv is None:
        raise HTTPException(status_code=404, detail="机器人不存在")
    return {"code": 0, "data": conv}


@router.delete("/{bot_id}")
async def remove_bot(request: Request, bot_id: str):
    if not await _manager(request).remove(bot_id):
        raise HTTPException(status_code=404, detail="机器人不存在")
    return {"code": 0, "data": {"deleted": bot_id}}
