"""群组后台任务 API：列表 + 暂停 / 继续 / 结束。

后台任务的成员 run 仍由 worker 执行；这里只做任务级控制，避免用户在会话区
误停整轮对话。执行画布通过本接口拿任务与成员 run 状态。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.agent_zero.services import team_service, team_task_service
from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer import conversation

router = APIRouter()


class CreateTeamTaskBody(BaseModel):
    goal: str = Field(..., min_length=1, max_length=8000)
    title: Optional[str] = Field(None, max_length=120)
    # 群组：指定成员；单角色会话忽略（任务由当前角色执行）。
    member_id: Optional[str] = Field(None, max_length=80)


def _task_or_404(conv_id: str, task_id: str):
    if not conversation.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    task = team_task_service.get_task(task_id)
    if not task or str(task.get("conversation_id") or "") != conv_id:
        raise HTTPException(status_code=404, detail=f"后台任务不存在: {task_id}")
    return task


@router.get("/conversations/{conv_id}/team-tasks")
def list_team_tasks(conv_id: str):
    if not conversation.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    items = team_task_service.list_tasks(conv_id)
    running = [item for item in items if item.get("status") == "running"]
    return {"code": 0, "data": {"items": items, "running": len(running)}}


@router.post("/conversations/{conv_id}/team-tasks")
def create_team_task(conv_id: str, body: CreateTeamTaskBody):
    """从会话 / 任务浮窗直接派发一个后台任务。

    - 群组：必须指定 `member_id`（派给该成员），主持负责记录；
    - 单角色：任务由当前角色自己执行（不需要成员）。
    结果：成员前台确认 + 后台任务 + 自动建工单。
    """
    conv = conversation.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    team = team_service.team_for_conv(conv)
    goal = body.goal.strip()
    title = (body.title or "").strip() or None
    if team:
        member = next((m for m in (team.get("members") or [])
                       if str(m.get("id") or "") == str(body.member_id or "")), None)
        if not member:
            raise HTTPException(status_code=400, detail="请选择要派发的群组成员")
        role = zero_store.get_role(str(member.get("id") or "")) or {
            "id": member.get("id"), "name": member.get("name") or "成员",
        }
        leader = zero_store.get_role(
            str((team.get("leader") or {}).get("id") or "")) or {
            "id": (team.get("leader") or {}).get("id"), "name": "主持",
        }
        assignment = {
            "role_id": str(role.get("id") or ""),
            "role_name": str(role.get("name") or "成员"),
            "goal": goal,
            "role": role,
        }
        try:
            task = team_task_service.create_task(
                conv, team, leader, [assignment], "local",
                title=title, mention=True)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        role_id = str(conv.get("role_id") or "").strip() or zero_store.DEFAULT_ROLE_ID
        role = zero_store.get_role(role_id) or {"id": role_id, "name": "助手"}
        assignment = {
            "role_id": role_id,
            "role_name": str(role.get("name") or "助手"),
            "goal": goal,
            "role": role,
        }
        try:
            task = team_task_service.create_task(
                conv, {}, role, [assignment], "local",
                title=title, mention=False)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": task}


@router.get("/conversations/{conv_id}/team-tasks/{task_id}")
def get_team_task(conv_id: str, task_id: str):
    return {"code": 0, "data": _task_or_404(conv_id, task_id)}


@router.post("/conversations/{conv_id}/team-tasks/{task_id}/pause")
def pause_team_task(conv_id: str, task_id: str):
    _task_or_404(conv_id, task_id)
    try:
        return {"code": 0, "data": team_task_service.pause_task(task_id)}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/conversations/{conv_id}/team-tasks/{task_id}/resume")
def resume_team_task(conv_id: str, task_id: str):
    _task_or_404(conv_id, task_id)
    try:
        return {"code": 0, "data": team_task_service.resume_task(task_id)}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/conversations/{conv_id}/team-tasks/{task_id}/stop")
def stop_team_task(conv_id: str, task_id: str):
    _task_or_404(conv_id, task_id)
    try:
        return {"code": 0, "data": team_task_service.stop_task(task_id)}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/conversations/{conv_id}/team-tasks/{task_id}/continue")
def continue_team_task(conv_id: str, task_id: str):
    """把已完成的后台任务结果交回主会话。

    任务结束（完成 / 失败）时系统会自动交回；这个接口是手动补交入口，
    自动交回没有发生时（例如异常），用户可以点「继续对话」。
    """
    _task_or_404(conv_id, task_id)
    try:
        return {"code": 0, "data": team_task_service.continue_task(task_id)}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

