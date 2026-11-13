"""工作区终端与后台脚本管理。"""

from __future__ import annotations

from ipaddress import ip_address
from subprocess import SubprocessError
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from services.agent_zero.services import terminal_service, workspace_service
from services.agent_zero.stores import zero_store
from services.agent_zero.task_layer import conversation, tool_jobs

router = APIRouter()


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def require_local_request(request: Request) -> None:
    origin = request.headers.get("origin")
    try:
        origin_host = urlsplit(origin).hostname if origin else None
    except ValueError:
        origin_host = None
    if (not request.client or not _is_loopback(request.client.host)
            or not _is_loopback(request.url.hostname or "")
            or (origin and not _is_loopback(origin_host or ""))
            or request.headers.get("sec-fetch-site") == "cross-site"):
        raise HTTPException(status_code=403, detail="此功能仅供本机 AtlasX 使用")


def _workspace_id(conversation_id: str, workspace_id: str) -> str:
    if conversation_id:
        conv = conversation.get_conversation(conversation_id)
        if not conv:
            raise HTTPException(status_code=404, detail="会话不存在")
        workspace_id = str(conv.get("workspace_id") or "")
    if not workspace_id:
        raise HTTPException(status_code=400, detail="请选择一个已绑定文件夹的工作区")
    if not zero_store.get_workspace(workspace_id):
        raise HTTPException(status_code=404, detail="工作区不存在")
    return workspace_id


class WorkspaceTarget(BaseModel):
    conversation_id: str = Field("", max_length=128)
    workspace_id: str = Field("", max_length=64)


class ScriptBody(WorkspaceTarget):
    name: str = Field("", max_length=160)
    command: str = Field(min_length=1, max_length=20_000)


@router.post("/local/terminal", dependencies=[Depends(require_local_request)])
def launch_terminal(body: WorkspaceTarget):
    workspace_id = _workspace_id(body.conversation_id, body.workspace_id)
    try:
        directory = workspace_service.get_workspace_root("group", workspace_id)
        terminal_service.open_terminal(directory)
    except workspace_service.WorkspacePathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, SubprocessError) as exc:
        raise HTTPException(status_code=503, detail=f"无法打开系统终端：{exc}") from exc
    return {"code": 0, "data": {"path": str(directory)}}


@router.get("/local/background-jobs", dependencies=[Depends(require_local_request)])
async def list_background_jobs(conversation_id: str | None = None):
    items = tool_jobs.list_jobs(conversation_id)
    return {"code": 0, "data": {"items": [
        {**tool_jobs.summary(job), "conversation_id": job["conversation_id"],
         "started_at": job["started_at"], "finished_at": job["finished_at"],
         "manual": job.get("manual", False)}
        for job in reversed(items)
    ]}}


@router.post("/local/background-jobs", dependencies=[Depends(require_local_request)])
async def start_background_job(body: ScriptBody):
    workspace_id = _workspace_id(body.conversation_id, body.workspace_id)
    try:
        directory = workspace_service.get_workspace_root("group", workspace_id)
        job = tool_jobs.start_script(body.conversation_id, directory, body.name, body.command)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"code": 0, "data": tool_jobs.summary(job)}


@router.get("/local/background-jobs/{job_id}", dependencies=[Depends(require_local_request)])
async def get_background_job(job_id: str):
    job = tool_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="后台任务不存在")
    return {"code": 0, "data": tool_jobs.summary(job, with_result=True)}


@router.post("/local/background-jobs/{job_id}/stop", dependencies=[Depends(require_local_request)])
async def stop_background_job(job_id: str):
    if not tool_jobs.get(job_id):
        raise HTTPException(status_code=404, detail="后台任务不存在")
    return {"code": 0, "data": {"stopped": tool_jobs.kill(job_id, reason="用户手动停止")}}
