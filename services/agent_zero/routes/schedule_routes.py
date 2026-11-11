"""定时 / 自由触发任务 API。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.agent_zero.services import schedule_service

router = APIRouter()


class ScheduleBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    prompt: str = Field(..., min_length=1, max_length=20000)
    trigger_mode: str = "scheduled"
    schedule_kind: str = "daily"
    schedule: Dict[str, Any] = Field(default_factory=dict)
    condition: str = ""
    workspace_id: Optional[str] = None
    role_id: Optional[str] = None
    conversation_id: Optional[str] = None
    run_mode: str = "chat"
    auto_execute: bool = False
    max_runs: int = 0
    meta: Dict[str, Any] = Field(default_factory=dict)


class SchedulePatch(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    prompt: Optional[str] = Field(None, min_length=1, max_length=20000)
    trigger_mode: Optional[str] = None
    schedule_kind: Optional[str] = None
    schedule: Optional[Dict[str, Any]] = None
    condition: Optional[str] = None
    workspace_id: Optional[str] = None
    role_id: Optional[str] = None
    conversation_id: Optional[str] = None
    run_mode: Optional[str] = None
    auto_execute: Optional[bool] = None
    max_runs: Optional[int] = None
    status: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


class PauseBody(BaseModel):
    paused: bool = True


@router.get("/schedules")
def list_schedules(status: Optional[str] = None, limit: int = 100):
    items = schedule_service.list_tasks(limit=limit, status=status)
    stats = {
        "total": len(items),
        "active": sum(1 for item in items if item.get("status") == "active"),
        "smart": sum(1 for item in items if item.get("trigger_mode") == "smart"),
        "paused": sum(1 for item in items if item.get("status") == "paused"),
    }
    return {"code": 0, "data": {"items": items, "stats": stats}}


@router.post("/schedules")
def create_schedule(body: ScheduleBody):
    try:
        item = schedule_service.create_task(body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": item}


@router.get("/schedules/{task_id}")
def get_schedule(task_id: str):
    item = schedule_service.get_task(task_id)
    if not item:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"code": 0, "data": item}


@router.patch("/schedules/{task_id}")
def patch_schedule(task_id: str, body: SchedulePatch):
    try:
        item = schedule_service.update_task(task_id, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": item}


@router.delete("/schedules/{task_id}")
def delete_schedule(task_id: str):
    if not schedule_service.delete_task(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"code": 0, "data": {"deleted": task_id}}


@router.post("/schedules/{task_id}/run")
async def run_schedule(task_id: str):
    try:
        item = await schedule_service.run_task(task_id, trigger="manual")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": item}


@router.post("/schedules/{task_id}/pause")
def pause_schedule(task_id: str, body: PauseBody):
    try:
        item = schedule_service.pause_task(task_id, paused=bool(body.paused))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": item}


@router.post("/schedules/{task_id}/evaluate")
async def evaluate_schedule(task_id: str):
    item = schedule_service.get_task(task_id)
    if not item:
        raise HTTPException(status_code=404, detail="任务不存在")
    trigger, reason = await schedule_service.evaluate(item)
    return {"code": 0, "data": {"trigger": trigger, "reason": reason}}
