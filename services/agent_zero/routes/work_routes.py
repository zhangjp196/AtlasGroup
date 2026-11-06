"""工单助手 工单 API：工单、准入策略、派活与 GitHub 来源。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.agent_zero.services import work_service

router = APIRouter()


class WorkItemBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    body: str = ""
    source: str = "manual"
    source_ref: Optional[str] = Field(None, max_length=500)
    source_actor: Optional[str] = Field(None, max_length=120)
    priority: int = 0
    workspace_id: Optional[str] = None
    role_id: Optional[str] = None
    team_id: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


class WorkItemPatch(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    body: Optional[str] = None
    source: Optional[str] = None
    source_ref: Optional[str] = Field(None, max_length=500)
    source_actor: Optional[str] = Field(None, max_length=120)
    priority: Optional[int] = None
    workspace_id: Optional[str] = None
    role_id: Optional[str] = None
    team_id: Optional[str] = None
    status: Optional[str] = None
    attention_reason: Optional[str] = None
    result_summary: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


class WorkPolicyBody(BaseModel):
    mode: Optional[str] = None
    permission_mode: Optional[str] = None
    auto_execute: Optional[bool] = None
    allowed_sources: Optional[List[str]] = None
    allowed_actors: Optional[List[str]] = None
    default_workspace_id: Optional[str] = None
    default_role_id: Optional[str] = None
    default_team_id: Optional[str] = None
    github_token: Optional[str] = None


@router.get("/work-items/settings")
def get_work_settings():
    return {"code": 0, "data": work_service.get_policy()}


@router.put("/work-items/settings")
def put_work_settings(body: WorkPolicyBody):
    try:
        data = work_service.save_policy(body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": data}


@router.get("/work-items")
def list_work_items(status: Optional[str] = None, limit: int = 100,
                    conversation_id: Optional[str] = None):
    return {"code": 0, "data": work_service.list_items(
        status=status, limit=limit, conversation_id=conversation_id)}


@router.post("/work-items")
async def create_work_item(body: WorkItemBody):
    try:
        item = work_service.create_item(body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    policy = work_service.get_policy()
    allowed, _ = work_service.admission(item, policy)
    if policy.get("mode") == "auto" and allowed and item.get("status") == "inbox":
        try:
            item = await work_service.dispatch_item(item["id"])
        except ValueError:
            # 自动派活失败不影响工单落库，工单会停在 inbox 等人工处理。
            item = work_service.get_item(item["id"]) or item
    return {"code": 0, "data": item}


@router.get("/work-items/{item_id}")
def get_work_item(item_id: str):
    item = work_service.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="工单不存在")
    return {"code": 0, "data": item}


@router.patch("/work-items/{item_id}")
def patch_work_item(item_id: str, body: WorkItemPatch):
    try:
        item = work_service.update_item(item_id, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": item}


@router.delete("/work-items/{item_id}")
def delete_work_item(item_id: str):
    if not work_service.delete_item(item_id):
        raise HTTPException(status_code=404, detail="工单不存在")
    return {"code": 0, "data": {"deleted": item_id}}


@router.post("/work-items/{item_id}/dispatch")
async def dispatch_work_item(item_id: str):
    try:
        item = await work_service.dispatch_item(item_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": item}
