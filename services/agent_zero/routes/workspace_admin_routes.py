"""工作区管理 API：添加本地文件夹、切换列表、归档 / 恢复、重命名、目录浏览。"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.agent_zero.services import (
    memory_service, vcs_service, workspace_service,
)
from services.agent_zero.stores import zero_store

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateWorkspaceBody(BaseModel):
    name: str = Field(default="", max_length=120)
    path: str = Field(..., min_length=1, max_length=2048)


class UpdateWorkspaceBody(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    archived: Optional[bool] = None


def _public_workspace(ws: Dict[str, Any]) -> Dict[str, Any]:
    """工作区对外视图。没有 root_path 的工作区是**纯聊天**工作区：无工作目录。"""
    ws_id = str(ws.get("id") or "")
    raw_root = str(ws.get("root_path") or "").strip()
    chat_only = not raw_root
    root: Optional[Path] = None
    if raw_root:
        root = Path(raw_root).expanduser()
        try:
            root = root.resolve()
        except OSError:
            root = root.absolute()
    if "conversation_count" in ws:
        count = int(ws.get("conversation_count") or 0)
    else:
        from services.agent_zero.task_layer import conversation as conv_mod

        count = conv_mod.count_conversations(workspace_id=ws_id)
    raw_recents = ws.get("recent_conversations")
    recents: List[Dict[str, Any]] = []
    if isinstance(raw_recents, str):
        try:
            recents = json.loads(raw_recents)
        except ValueError:
            recents = []
    elif isinstance(raw_recents, list):
        recents = raw_recents
    return {
        "id": ws_id,
        "name": str(ws.get("name") or ws_id),
        "kind": str(ws.get("kind") or "group"),
        "root_path": str(root) if root else "",
        "root_exists": bool(root) and root.is_dir(),
        "chat_only": chat_only,
        "managed": chat_only,
        "archived": bool(ws.get("archived")),
        "conversation_count": count,
        "created_at": str(ws.get("created_at") or ""),
        "last_conv_updated": str(ws.get("last_conv_updated") or ""),
        "recent_conversations": [
            {
                "id": str(r.get("id") or ""),
                "title": str(r.get("title") or ""),
                "updated_at": str(r.get("updated_at") or ""),
                "active": bool(r.get("active")),
            }
            for r in recents
        ],
        "vcs": vcs_service.detect(ws_id) if root else None,
    }


def _get_or_404(ws_id: str) -> Dict[str, Any]:
    ws = zero_store.get_workspace(ws_id)
    if not ws or ws_id == zero_store.DEFAULT_WORKSPACE_ID:
        raise HTTPException(status_code=404, detail=f"工作区不存在: {ws_id}")
    return ws


def _workspace_by_root(root: Path) -> Optional[Dict[str, Any]]:
    target = str(root.resolve())
    for item in zero_store.list_workspaces(limit=500, include_archived=True):
        raw = str(item.get("root_path") or "").strip()
        if not raw:
            continue  # 纯聊天工作区没有目录，不可能重复
        current = Path(raw).expanduser()
        try:
            if str(current.resolve()) == target:
                return item
        except OSError:
            continue
    return None


@router.get("/workspaces")
def list_workspaces(include_archived: bool = Query(False),
                    limit: int = Query(500, ge=1, le=500),
                    offset: int = Query(0, ge=0),
                    keyword: Optional[str] = Query(None, max_length=100)):
    items = zero_store.list_workspaces(limit=limit, offset=offset,
                                       include_archived=include_archived,
                                       keyword=keyword)
    return {"code": 0, "data": {
        "items": [_public_workspace(w) for w in items],
        "total": zero_store.count_workspaces(include_archived=include_archived,
                                            keyword=keyword),
    }}


@router.get("/workspaces/browse")
def browse_folders(path: Optional[str] = Query(None, max_length=2048)):
    raw = (path or "").strip() or str(Path.home())
    target = Path(os.path.expanduser(raw))
    if not target.is_absolute():
        raise HTTPException(status_code=400, detail=f"请使用绝对路径: {raw}")
    try:
        target = target.resolve()
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"文件夹不可用: {raw}") from e
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"文件夹不存在: {raw}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"不是文件夹: {raw}")

    items: List[Dict[str, Any]] = []
    file_count = 0
    try:
        # 隐藏目录排在后面，避免主目录以一堆 .xxx 开头。
        children = sorted(
            target.iterdir(),
            key=lambda p: (p.name.startswith("."), p.name.lower()),
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=f"无法读取文件夹: {raw}") from e
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"无法读取文件夹: {raw}") from e
    for child in children:
        try:
            if not child.is_dir():
                file_count += 1
                continue
        except OSError:
            continue
        items.append({
            "name": child.name,
            "path": str(child),
            "hidden": child.name.startswith("."),
        })

    parent = target.parent
    return {"code": 0, "data": {
        "path": str(target),
        "name": target.name or str(target),
        "parent": str(parent) if parent != target else None,
        "home": str(Path.home()),
        "file_count": file_count,
        "items": items,
    }}


@router.post("/workspaces")
def create_workspace(body: CreateWorkspaceBody):
    raw = os.path.expanduser(body.path.strip())
    target = Path(raw)
    if not target.is_absolute():
        raise HTTPException(status_code=400, detail=f"请使用绝对路径: {raw}")
    try:
        target = target.resolve()
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"文件夹不可用: {raw}") from e
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"文件夹不存在: {raw}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"不是文件夹: {raw}")

    duplicate = _workspace_by_root(target)
    if duplicate:
        if duplicate.get("archived"):
            # 删除只做隐藏：重新添加同一目录时恢复原工作区（会话/文件/记忆都还在）。
            zero_store.update_workspace(duplicate["id"], {"archived": False})
            try:
                workspace_service.ensure_workspace("group", duplicate["id"])
            except workspace_service.WorkspacePathError as e:
                raise HTTPException(status_code=400, detail=str(e))
            ws = _get_or_404(duplicate["id"])
            ws["conversation_count"] = int(duplicate.get("conversation_count") or 0)
            data = _public_workspace(ws)
            data["restored"] = True
            return {"code": 0, "data": data}
        raise HTTPException(
            status_code=409,
            detail=f"该文件夹已作为工作区存在: {duplicate.get('name') or duplicate.get('id')}",
        )

    name = body.name.strip() or target.name or str(target)
    ws_id = zero_store.new_id("ws")
    # V3：工作区只是文件 / 终端容器，不预置群成员。
    # 旧版本把默认角色写进 member_role_ids，惰性归一后每个工作区都凭空
    # 出现一个「默认群聊」，单聊被当成群聊。
    zero_store.create_workspace(
        "group", ws_id, name, "local",
        origin={"root": str(target), "source": "folder-picker"},
        root_path=str(target),
    )
    zero_store.update_workspace(ws_id, {"host_role_id": zero_store.DEFAULT_ROLE_ID})
    try:
        workspace_service.ensure_workspace("group", ws_id)
    except workspace_service.WorkspacePathError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ws = _get_or_404(ws_id)
    ws["conversation_count"] = 0
    return {"code": 0, "data": _public_workspace(ws)}


@router.delete("/workspaces/{ws_id}")
def delete_workspace(ws_id: str):
    """删除工作区 = 仅从列表隐藏（归档）。目录、会话、记忆、文件一律保留；
    之后重新添加同一目录会自动恢复该工作区。"""
    ws = _get_or_404(ws_id)
    zero_store.update_workspace(ws_id, {"archived": True})
    full = zero_store.list_workspaces(limit=500, include_archived=True)
    row = next((w for w in full if w.get("id") == ws_id), ws)
    return {"code": 0, "data": _public_workspace(row)}


@router.get("/workspaces/{ws_id}")
def get_workspace(ws_id: str):
    return {"code": 0, "data": _public_workspace(_get_or_404(ws_id))}


@router.patch("/workspaces/{ws_id}")
def update_workspace(ws_id: str, body: UpdateWorkspaceBody):
    ws = _get_or_404(ws_id)
    fields: Dict[str, Any] = {}
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="工作区名称不能为空")
        fields["name"] = name
    if body.archived is not None:
        fields["archived"] = bool(body.archived)
    if not fields:
        return {"code": 0, "data": _public_workspace(ws)}
    updated = zero_store.update_workspace(ws_id, fields) or ws
    full = zero_store.list_workspaces(limit=500, include_archived=True)
    row = next((w for w in full if w.get("id") == ws_id), updated)
    return {"code": 0, "data": _public_workspace(row)}


# ── 版本控制（git / svn）────────────────────────────────────────────

class VcsCommitBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    paths: Optional[List[str]] = Field(default=None, max_length=200)
    all: bool = Field(default=False)


class VcsSwitchBody(BaseModel):
    branch: str = Field(..., min_length=1, max_length=120)
    create: bool = Field(default=False)


class VcsPathsBody(BaseModel):
    paths: List[str] = Field(..., min_length=1, max_length=200)


class VcsRemoteBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    url: str = Field(..., min_length=1, max_length=2048)


class VcsRemoteUpdateBody(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    push: bool = Field(default=False)


class VcsFetchBody(BaseModel):
    remote: Optional[str] = Field(default=None, max_length=64)


@router.get("/workspaces/{ws_id}/vcs")
def vcs_summary(ws_id: str):
    _get_or_404(ws_id)
    try:
        return {"code": 0, "data": vcs_service.summary(ws_id)}
    except (vcs_service.VcsError, workspace_service.WorkspacePathError) as e:
        return {"code": 0, "data": {"type": None, "error": str(e)}}


@router.get("/workspaces/{ws_id}/vcs/status")
def vcs_status(ws_id: str):
    _get_or_404(ws_id)
    try:
        return {"code": 0, "data": vcs_service.status(ws_id)}
    except (vcs_service.VcsError, workspace_service.WorkspacePathError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/workspaces/{ws_id}/vcs/log")
def vcs_log(ws_id: str, limit: int = Query(20, ge=1, le=100)):
    _get_or_404(ws_id)
    try:
        return {"code": 0, "data": vcs_service.log(ws_id, limit=limit)}
    except (vcs_service.VcsError, workspace_service.WorkspacePathError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/workspaces/{ws_id}/vcs/diff")
def vcs_diff(ws_id: str, path: Optional[str] = Query(None, max_length=512),
             rev: Optional[str] = Query(None, max_length=64)):
    _get_or_404(ws_id)
    try:
        return {"code": 0, "data": vcs_service.diff(ws_id, path=path, rev=rev)}
    except (vcs_service.VcsError, workspace_service.WorkspacePathError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/workspaces/{ws_id}/vcs/branches")
def vcs_branches(ws_id: str):
    _get_or_404(ws_id)
    try:
        return {"code": 0, "data": vcs_service.branches(ws_id)}
    except (vcs_service.VcsError, workspace_service.WorkspacePathError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/workspaces/{ws_id}/vcs/remotes")
def vcs_remotes(ws_id: str):
    _get_or_404(ws_id)
    try:
        return {"code": 0, "data": vcs_service.remotes(ws_id)}
    except (vcs_service.VcsError, workspace_service.WorkspacePathError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/workspaces/{ws_id}/vcs/remotes")
def vcs_remote_add(ws_id: str, body: VcsRemoteBody):
    _get_or_404(ws_id)
    try:
        return {"code": 0, "data": vcs_service.remote_add(ws_id, body.name, body.url)}
    except (vcs_service.VcsError, workspace_service.WorkspacePathError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/workspaces/{ws_id}/vcs/remotes/{name}")
def vcs_remote_update(ws_id: str, name: str, body: VcsRemoteUpdateBody):
    _get_or_404(ws_id)
    try:
        return {"code": 0, "data": vcs_service.remote_set_url(
            ws_id, name, body.url, push=body.push)}
    except (vcs_service.VcsError, workspace_service.WorkspacePathError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/workspaces/{ws_id}/vcs/remotes/{name}")
def vcs_remote_remove(ws_id: str, name: str):
    _get_or_404(ws_id)
    try:
        return {"code": 0, "data": vcs_service.remote_remove(ws_id, name)}
    except (vcs_service.VcsError, workspace_service.WorkspacePathError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/workspaces/{ws_id}/vcs/fetch")
def vcs_fetch(ws_id: str, body: VcsFetchBody):
    _get_or_404(ws_id)
    try:
        return {"code": 0, "data": vcs_service.fetch(ws_id, body.remote)}
    except (vcs_service.VcsError, workspace_service.WorkspacePathError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/workspaces/{ws_id}/vcs/switch")
def vcs_switch(ws_id: str, body: VcsSwitchBody):
    _get_or_404(ws_id)
    try:
        return {"code": 0, "data": vcs_service.switch(ws_id, body.branch,
                                                     create=body.create)}
    except (vcs_service.VcsError, workspace_service.WorkspacePathError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/workspaces/{ws_id}/vcs/commit")
def vcs_commit(ws_id: str, body: VcsCommitBody):
    _get_or_404(ws_id)
    try:
        return {"code": 0, "data": vcs_service.commit(ws_id, body.message,
                                                     paths=body.paths,
                                                     all_changes=body.all)}
    except (vcs_service.VcsError, workspace_service.WorkspacePathError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/workspaces/{ws_id}/vcs/stage")
def vcs_stage(ws_id: str, body: VcsPathsBody):
    _get_or_404(ws_id)
    try:
        return {"code": 0, "data": vcs_service.stage(ws_id, body.paths)}
    except (vcs_service.VcsError, workspace_service.WorkspacePathError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/workspaces/{ws_id}/vcs/unstage")
def vcs_unstage(ws_id: str, body: VcsPathsBody):
    _get_or_404(ws_id)
    try:
        return {"code": 0, "data": vcs_service.unstage(ws_id, body.paths)}
    except (vcs_service.VcsError, workspace_service.WorkspacePathError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/workspaces/{ws_id}/vcs/discard")
def vcs_discard(ws_id: str, body: VcsPathsBody):
    _get_or_404(ws_id)
    try:
        return {"code": 0, "data": vcs_service.discard(ws_id, body.paths)}
    except (vcs_service.VcsError, workspace_service.WorkspacePathError) as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── 记忆管理（列表 / 图谱 / 统计 / 重建 / 清空）──────────────────

@router.get("/workspaces/{ws_id}/memories")
def workspace_memories(ws_id: str, q: Optional[str] = Query(None, max_length=200),
                      kind: Optional[str] = Query(None, max_length=20),
                      source: Optional[str] = Query(None, max_length=20),
                      sort: str = Query("recent", max_length=20),
                      limit: int = Query(20, ge=1, le=100),
                      offset: int = Query(0, ge=0),
                      include_archived: bool = Query(False)):
    _get_or_404(ws_id)
    return {"code": 0, "data": memory_service.list_memories(
        ws_id, kind=kind, source=source, q=q, sort=sort, limit=limit, offset=offset,
        include_archived=include_archived)}


@router.get("/workspaces/{ws_id}/memories/stats")
def workspace_memory_stats(ws_id: str):
    _get_or_404(ws_id)
    return {"code": 0, "data": memory_service.memory_stats(ws_id)}


@router.get("/workspaces/{ws_id}/memories/graph")
def workspace_memory_graph(ws_id: str, limit: int = Query(150, ge=1, le=300)):
    _get_or_404(ws_id)
    return {"code": 0, "data": memory_service.memory_graph(ws_id, limit=limit)}


@router.get("/workspaces/{ws_id}/memories/export")
def workspace_memory_export(ws_id: str):
    _get_or_404(ws_id)
    return {"code": 0, "data": {"markdown": memory_service.export_markdown(ws_id)}}


@router.post("/workspaces/{ws_id}/memories/rebuild")
def rebuild_memories(ws_id: str):
    """启动后台记忆重建（LLM 蒸馏，立即返回，不阻塞其它请求）。"""
    _get_or_404(ws_id)
    return {"code": 0, "data": memory_service.start_rebuild(ws_id)}


@router.get("/workspaces/{ws_id}/memories/rebuild/status")
def rebuild_memories_status(ws_id: str):
    _get_or_404(ws_id)
    return {"code": 0, "data": memory_service.rebuild_status(ws_id)}


@router.post("/workspaces/{ws_id}/memories/consolidate")
def consolidate_memories(ws_id: str):
    """整理记忆：合并近重复 + 归档低价值/超量项（同步、幂等）。"""
    _get_or_404(ws_id)
    return {"code": 0, "data": memory_service.consolidate(ws_id)}


@router.delete("/workspaces/{ws_id}/memories")
def clear_memories(ws_id: str):
    _get_or_404(ws_id)
    return {"code": 0, "data": memory_service.clear_memories(ws_id)}



