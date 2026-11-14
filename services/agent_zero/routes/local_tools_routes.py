"""工作区终端与后台脚本管理。"""

from __future__ import annotations

import asyncio
import json
import os
import struct
from ipaddress import ip_address
from subprocess import SubprocessError
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from services.agent_zero.services import browser_service, terminal_service, workspace_service
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


class BrowserCommandResult(BaseModel):
    result: object = None
    error: str = Field("", max_length=4000)


@router.get("/local/browser/next", dependencies=[Depends(require_local_request)])
async def next_browser_command():
    return {"code": 0, "data": await browser_service.next_command()}


@router.post("/local/browser/heartbeat", dependencies=[Depends(require_local_request)])
async def browser_client_heartbeat():
    browser_service.heartbeat()
    return {"code": 0, "data": {"available": True}}


@router.post("/local/browser/{command_id}/result", dependencies=[Depends(require_local_request)])
async def browser_command_result(command_id: str, body: BrowserCommandResult):
    completed = browser_service.complete(
        command_id, result=body.result, error=body.error)
    return {"code": 0, "data": {"accepted": completed}}


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


@router.websocket("/local/terminal/ws")
async def terminal_websocket(websocket: WebSocket):
    """在工作区目录启动 PTY，并通过 WebSocket 暴露交互式 shell。"""
    client_host = websocket.client.host if websocket.client else ""
    origin = websocket.headers.get("origin")
    try:
        origin_host = urlsplit(origin).hostname if origin else None
    except ValueError:
        origin_host = None
    if (not _is_loopback(client_host) or not _is_loopback(websocket.url.hostname or "")
            or (origin and not _is_loopback(origin_host or ""))
            or websocket.headers.get("sec-fetch-site") == "cross-site"):
        await websocket.close(code=4403, reason="仅允许本机 AtlasX 使用")
        return
    from pathlib import Path
    workspace_id = str(websocket.query_params.get("workspace_id") or "")
    if not workspace_id or not zero_store.get_workspace(workspace_id):
        await websocket.close(code=4404, reason="工作区不存在")
        return
    try:
        directory = workspace_service.exec_root(workspace_id)
    except workspace_service.WorkspacePathError as exc:
        await websocket.close(code=4400, reason=str(exc)[:120])
        return

    unix_pty = os.name != "nt"
    master_fd = slave_fd = None
    if unix_pty:
        import fcntl
        import pty
        import signal
        import termios

        master_fd, slave_fd = pty.openpty()
        shell = os.environ.get("SHELL") or "/bin/zsh"
        if not Path(shell).is_file():
            shell = "/bin/sh"
        command = [shell, "-l"]
        env = {**os.environ, "TERM": "xterm-256color", "PWD": str(directory)}
    else:
        shell = os.environ.get("COMSPEC") or "powershell.exe"
        command = [shell]
        env = {**os.environ}
    try:
        if unix_pty:
            process = await asyncio.create_subprocess_exec(
                *command, cwd=str(directory), env=env,
                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                start_new_session=True,
            )
        else:
            process = await asyncio.create_subprocess_exec(
                *command, cwd=str(directory), env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
    except (OSError, SubprocessError) as exc:
        if master_fd is not None:
            os.close(master_fd)
        if slave_fd is not None:
            os.close(slave_fd)
        await websocket.close(code=4503, reason=f"无法启动 shell：{exc}"[:120])
        return
    if unix_pty:
        os.close(slave_fd)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 120, 0, 0))
    await websocket.accept()

    async def send_output() -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                if unix_pty:
                    data = await loop.run_in_executor(None, os.read, master_fd, 8192)
                else:
                    data = await process.stdout.read(8192)
            except OSError:
                return
            if not data:
                try:
                    await websocket.close(code=1000, reason="shell exited")
                except RuntimeError:
                    pass
                return
            try:
                await websocket.send_bytes(data)
            except RuntimeError:
                return

    output_task = asyncio.create_task(send_output())
    try:
        while True:
            message = await websocket.receive_text()
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                continue
            if payload.get("type") == "input":
                data = str(payload.get("data") or "").encode("utf-8", errors="replace")
                if data:
                    if unix_pty:
                        os.write(master_fd, data)
                    elif process.stdin:
                        process.stdin.write(data)
                        await process.stdin.drain()
            elif payload.get("type") == "resize" and unix_pty:
                cols = max(20, min(500, int(payload.get("cols") or 120)))
                rows = max(5, min(200, int(payload.get("rows") or 30)))
                fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
                os.killpg(process.pid, signal.SIGWINCH)
    except (WebSocketDisconnect, OSError, ValueError):
        pass
    finally:
        output_task.cancel()
        if unix_pty:
            try:
                os.killpg(process.pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
        elif process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=1)
        except asyncio.TimeoutError:
            if unix_pty:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
        if master_fd is not None:
            os.close(master_fd)
        await asyncio.gather(output_task, return_exceptions=True)


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
