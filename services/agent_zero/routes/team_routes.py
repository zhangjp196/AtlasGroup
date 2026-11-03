"""团队（= IM 里的「群」）管理 API：CRUD + 会话绑定。

V3 起团队是一等公民：界面通过本路由创建 / 编辑 / 删除群，模型仍可用
`team_manage` 工具做同样的事，两边改的是同一份数据。

- `GET    /teams?workspace_id=...`   列表（含惰性归一出的默认团队）
- `POST   /teams`                    创建（name / mode / leader / members）
- `GET    /teams/{id}`               详情
- `PATCH  /teams/{id}`               改 name / description / mode / leader / members
- `DELETE /teams/{id}`               删除（解绑会话 + 删非系统角色）
- `POST   /teams/{id}/bind`          绑定会话
- `POST   /teams/{id}/unbind`        解绑会话
- `GET    /conversations/{id}/team`  当前绑定
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.agent_zero.services import team_service
from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer import conversation

logger = logging.getLogger(__name__)

router = APIRouter()

_MODE_PATTERN = "^(orchestrate|roundtable|single)$"


class RoleSpecBody(BaseModel):
    """主持 / 成员：可以是已有角色（只给 id），也可以现场创建（name + expertise）。"""

    id: Optional[str] = Field(None, max_length=80)
    name: Optional[str] = Field(None, max_length=120)
    expertise: Optional[str] = Field(None, max_length=600)
    tools: Optional[List[str]] = Field(None, max_length=40)
    model_code: Optional[str] = Field(None, max_length=80)


class TeamCreateBody(BaseModel):
    # V3：群组独立于工作区，可不传；传了只作为历史来源保存。
    workspace_id: Optional[str] = Field(None, max_length=80)
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=1000)
    mode: Optional[str] = Field(None, pattern=_MODE_PATTERN)
    max_speakers: Optional[int] = Field(None, ge=1, le=12)
    leader: Optional[RoleSpecBody] = None
    members: Optional[List[RoleSpecBody]] = Field(None, max_length=12)


class TeamUpdateBody(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=1000)
    mode: Optional[str] = Field(None, pattern=_MODE_PATTERN)
    max_speakers: Optional[int] = Field(None, ge=1, le=12)
    leader: Optional[RoleSpecBody] = None
    members: Optional[List[RoleSpecBody]] = Field(None, max_length=12)


class BindBody(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=80)


def _spec(spec: Optional[RoleSpecBody]) -> Optional[Dict[str, Any]]:
    return spec.model_dump(exclude_none=True) if spec is not None else None


@router.get("/teams")
def list_teams(workspace_id: Optional[str] = Query(None, max_length=80)):
    """群组列表。V3.3 起群组独立：不再按工作区旧群组列自动造「默认群聊」。"""
    if workspace_id:
        ws_id = str(workspace_id).strip()
        if not zero_store.get_workspace(ws_id):
            raise HTTPException(status_code=404, detail=f"工作区不存在: {ws_id}")
        items = team_service.list_teams(ws_id)
    else:
        items = team_service.list_teams(None)
    return {"code": 0, "data": {"items": items}}


@router.post("/teams")
def create_team(body: TeamCreateBody):
    ws_id = str(body.workspace_id or "").strip() or zero_store.DEFAULT_WORKSPACE_ID
    if not zero_store.get_workspace(ws_id):
        raise HTTPException(status_code=404, detail=f"工作区不存在: {ws_id}")
    try:
        team = team_service.create_team(
            ws_id, body.name, body.description or "",
            leader=_spec(body.leader),
            members=[m for m in (_spec(x) for x in (body.members or [])) if m],
            mode=body.mode, max_speakers=body.max_speakers,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": team}


@router.get("/teams/{team_id}")
def get_team(team_id: str):
    team = team_service.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail=f"团队不存在: {team_id}")
    return {"code": 0, "data": team}


@router.patch("/teams/{team_id}")
def update_team(team_id: str, body: TeamUpdateBody):
    if not team_service.get_team(team_id):
        raise HTTPException(status_code=404, detail=f"团队不存在: {team_id}")
    try:
        team = team_service.update_team(
            team_id,
            name=body.name,
            description=body.description,
            leader=_spec(body.leader),
            members=[m for m in (_spec(x) for x in (body.members or [])) if m]
            if body.members is not None else None,
            mode=body.mode,
            max_speakers=body.max_speakers,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": team}


@router.delete("/teams/{team_id}")
def delete_team(team_id: str):
    if not team_service.delete_team(team_id):
        raise HTTPException(status_code=404, detail=f"团队不存在: {team_id}")
    return {"code": 0, "data": {"deleted": True, "id": team_id}}


@router.post("/teams/{team_id}/bind")
def bind_team(team_id: str, body: BindBody):
    team = team_service.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail=f"团队不存在: {team_id}")
    conv = conversation.get_conversation(body.conversation_id)
    if not conv:
        raise HTTPException(status_code=404,
                            detail=f"对话不存在: {body.conversation_id}")
    if str(conv.get("workspace_id") or "") != str(team.get("workspace_id") or ""):
        raise HTTPException(status_code=400,
                            detail="团队与对话不属于同一个工作区")
    team_service.bind_team(body.conversation_id, team_id)
    return {"code": 0, "data": {"bound": True, "team_id": team_id,
                                "conversation_id": body.conversation_id}}


@router.post("/teams/{team_id}/unbind")
def unbind_team(team_id: str, body: BindBody):
    team = team_service.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail=f"团队不存在: {team_id}")
    team_service.bind_team(
        body.conversation_id, None,
        notice=f"你已退出群聊「{team.get('name') or team_id}」")
    return {"code": 0, "data": {"bound": False,
                                "conversation_id": body.conversation_id}}


@router.get("/conversations/{conv_id}/team")
def conversation_team(conv_id: str):
    conv = conversation.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    team = team_service.team_for_conv(conv)
    return {"code": 0, "data": {"team": team,
                                "team_id": str(conv.get("team_id") or "")}}
