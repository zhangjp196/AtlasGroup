"""Workspace 文件 API（本地单用户，默认绑定 127.0.0.1）。

读写都有：`GET /file|/list|/file/preview|/file/download` 是读，
`PUT /file`、`DELETE /file`、`/rename`、`/mkdir`、`/download/zip` 会**改磁盘内容**。
没有任何鉴权，安全边界全靠 `server.host` 保持回环地址——把它改成 0.0.0.0
等于把这些写接口暴露给局域网，`webapp.serve()` 会在此时打 warn 日志。
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from services.agent_zero.services.preview_service import preview_file
from services.agent_zero.services.workspace_service import (
    WorkspacePathError,
    build_workspace_zip,
    create_directory,
    delete_file,
    get_workspace_root,
    list_dir,
    read_file,
    rename_entry,
    resolve_file_for_download,
    reveal_in_file_manager,
    write_file,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class WorkspaceWriteBody(BaseModel):
    workspace_id: str = Field(..., min_length=1, max_length=64)
    path: str = Field(..., min_length=1, max_length=1024)
    content: str = Field(default="")


class WorkspaceEntryBody(BaseModel):
    workspace_id: str = Field(..., min_length=1, max_length=64)
    path: str = Field(..., min_length=1, max_length=1024)
    name: Optional[str] = Field(None, max_length=255)


class WorkspaceDownloadBody(BaseModel):
    workspace_id: str = Field(..., min_length=1, max_length=64)
    paths: List[str] = Field(default_factory=list)


@router.get("/info")
def workspace_info(workspace_id: str = Query(...)):
    try:
        root = get_workspace_root("group", workspace_id)
    except WorkspacePathError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": {
        "workspace_id": workspace_id,
        "path": str(root),
        "root_path": str(root),
    }}


@router.get("/list")
def workspace_list(workspace_id: str = Query(...), path: str = Query(".")):
    try:
        return {"code": 0, "data": list_dir(workspace_id, path, include_hidden=True)}
    except WorkspacePathError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/file")
def workspace_read(workspace_id: str = Query(...), path: str = Query(...)):
    try:
        return {"code": 0, "data": read_file(workspace_id, path)}
    except WorkspacePathError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/file/preview")
def workspace_preview(workspace_id: str = Query(...), path: str = Query(...)):
    try:
        return {"code": 0, "data": preview_file(workspace_id, path)}
    except WorkspacePathError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/file")
def workspace_write(body: WorkspaceWriteBody):
    try:
        return {"code": 0, "data": write_file(body.workspace_id, body.path, body.content)}
    except WorkspacePathError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/file")
def workspace_delete(workspace_id: str = Query(...), path: str = Query(...)):
    try:
        return {"code": 0, "data": delete_file(workspace_id, path)}
    except WorkspacePathError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/reveal")
def workspace_reveal(body: WorkspaceEntryBody):
    """在 Finder / 资源管理器中定位条目。"""
    try:
        return {"code": 0, "data": reveal_in_file_manager(body.workspace_id, body.path)}
    except WorkspacePathError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/rename")
def workspace_rename(body: WorkspaceEntryBody):
    if not body.name:
        raise HTTPException(status_code=400, detail="缺少新名称")
    try:
        return {"code": 0, "data": rename_entry(body.workspace_id, body.path, body.name)}
    except WorkspacePathError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/mkdir")
def workspace_mkdir(body: WorkspaceEntryBody):
    if not body.name:
        raise HTTPException(status_code=400, detail="缺少目录名称")
    parent = body.path if body.path not in ("", ".") else None
    try:
        return {"code": 0, "data": create_directory(body.workspace_id, parent, body.name)}
    except WorkspacePathError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/file/download")
def workspace_download(workspace_id: str = Query(...), path: str = Query(...)):
    try:
        target = resolve_file_for_download(workspace_id, path)
    except WorkspacePathError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return FileResponse(target, filename=target.name)


@router.post("/download/zip")
def workspace_download_zip(body: WorkspaceDownloadBody):
    try:
        buf = build_workspace_zip(body.workspace_id, body.paths)
    except WorkspacePathError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not buf:
        raise HTTPException(status_code=400, detail="没有可打包的文件")
    filename = f"{body.workspace_id}-files.zip"
    return Response(
        content=buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
