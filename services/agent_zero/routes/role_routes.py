"""角色库 API（V3：角色独立于工作区与群组）。

- 持久角色：跨会话复用，存 `roles`（scope=persistent）；
- 临时角色：只属于一个会话（scope=temporary, session_id=会话 id），会话删除时清理；
- 来源：user / llm（模型在对话里创建）。

群组只是「角色集合」，角色本身不挂在任何工作区或群组上。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.agent_zero.stores import zero_store

logger = logging.getLogger(__name__)

router = APIRouter()

SCOPES = ("persistent", "temporary")
SOURCES = ("user", "llm")
_PROTECTED = {zero_store.DEFAULT_ROLE_ID}


class RoleCreateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    persona: Optional[str] = Field(None, max_length=4000)
    expertise: Optional[str] = Field(None, max_length=1000)
    avatar: Optional[str] = Field(None, max_length=200)
    tools: Optional[List[str]] = Field(None, max_length=40)
    model_code: Optional[str] = Field(None, max_length=80)
    scope: Optional[str] = Field(None, pattern="^(persistent|temporary)$")
    source: Optional[str] = Field(None, pattern="^(user|llm)$")
    session_id: Optional[str] = Field(None, max_length=80)


class RoleUpdateBody(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    persona: Optional[str] = Field(None, max_length=4000)
    expertise: Optional[str] = Field(None, max_length=1000)
    avatar: Optional[str] = Field(None, max_length=200)
    tools: Optional[List[str]] = Field(None, max_length=40)
    model_code: Optional[str] = Field(None, max_length=80)
    scope: Optional[str] = Field(None, pattern="^(persistent|temporary)$")
    session_id: Optional[str] = Field(None, max_length=80)


def _public(role: Dict[str, Any]) -> Dict[str, Any]:
    envelope = role.get("envelope") if isinstance(role.get("envelope"), dict) else {}
    tools = [str(t) for t in (envelope.get("tools") or [])]
    return {
        "id": role.get("id"),
        "name": str(role.get("name") or role.get("id") or ""),
        "persona": str(role.get("persona") or ""),
        "expertise": str(role.get("expertise") or ""),
        "avatar": str(role.get("avatar") or ""),
        "tools": tools,
        "model_code": str(role.get("model_code") or ""),
        "scope": str(role.get("scope") or "persistent"),
        "source": str(role.get("source") or "user"),
        "session_id": str(role.get("session_id") or ""),
        "created_at": str(role.get("created_at") or ""),
        "system": str(role.get("id") or "") in _PROTECTED,
    }


def _envelope(tools: Optional[List[str]], current: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """按请求里的 tools 生成能力包络；没传则沿用当前包络。"""
    if tools is None:
        env = (current or {}).get("envelope")
        return dict(env) if isinstance(env, dict) else dict(zero_store.DEFAULT_ENVELOPE)
    try:
        from services.agent_zero.tools import registry

        known = set(registry.known_codes())
        picked = [registry.canonical(str(t)) for t in tools if str(t).strip()]
        picked = [t for t in dict.fromkeys(picked) if t in known]
    except Exception:
        picked = [str(t) for t in tools if str(t).strip()]
    env = dict(zero_store.DEFAULT_ENVELOPE)
    if picked:
        env["tools"] = picked
    return env


def _get_or_404(role_id: str) -> Dict[str, Any]:
    role = zero_store.get_role(role_id)
    if not role:
        raise HTTPException(status_code=404, detail=f"角色不存在: {role_id}")
    return role


@router.get("/roles")
def list_roles(include_temporary: bool = Query(False),
               session_id: Optional[str] = Query(None, max_length=80)):
    """角色库。默认只返回持久角色；带 session_id 时额外返回该会话的临时角色。"""
    rows = zero_store.list_roles(limit=200)
    out: List[Dict[str, Any]] = []
    for row in rows:
        item = _public(row)
        if item["scope"] == "temporary":
            if not include_temporary:
                continue
            if session_id and item["session_id"] != session_id:
                continue
        out.append(item)
    return {"code": 0, "data": {"items": out}}


@router.post("/roles")
def create_role(body: RoleCreateBody):
    scope = body.scope or "persistent"
    session_id = str(body.session_id or "").strip() or None
    if scope == "temporary" and not session_id:
        raise HTTPException(status_code=400,
                            detail="临时角色必须带 session_id（所属会话）")
    if scope not in SCOPES or (body.source or "user") not in SOURCES:
        raise HTTPException(status_code=400, detail="非法 scope / source")
    role = zero_store.upsert_role({
        "id": zero_store.new_id("role"),
        "group_id": None,
        "name": body.name,
        "persona": body.persona or "",
        "expertise": body.expertise or "",
        "avatar": body.avatar,
        "envelope": _envelope(body.tools, None),
        "model_code": body.model_code,
        "scope": scope,
        "source": body.source or "user",
        "session_id": session_id,
    })
    return {"code": 0, "data": _public(role)}


@router.patch("/roles/{role_id}")
def update_role(role_id: str, body: RoleUpdateBody):
    current = _get_or_404(role_id)
    scope = body.scope or str(current.get("scope") or "persistent")
    session_id = (str(body.session_id).strip() or None
                  if body.session_id is not None else current.get("session_id"))
    if scope == "temporary" and not session_id:
        raise HTTPException(status_code=400,
                            detail="临时角色必须带 session_id（所属会话）")
    if scope == "persistent":
        session_id = ""   # 显式清空（upsert 里 NULL 表示“保持不变”）
    role = zero_store.upsert_role({
        "id": role_id,
        "group_id": current.get("group_id"),
        "name": body.name or current.get("name"),
        "persona": body.persona if body.persona is not None else current.get("persona"),
        "expertise": (body.expertise if body.expertise is not None
                      else current.get("expertise")),
        "avatar": body.avatar if body.avatar is not None else current.get("avatar"),
        "envelope": _envelope(body.tools, current),
        "model_code": (body.model_code if body.model_code is not None
                       else current.get("model_code")),
        "scope": scope,
        "source": current.get("source") or "user",
        "session_id": session_id,
    })
    return {"code": 0, "data": _public(role)}


@router.delete("/roles/{role_id}")
def delete_role(role_id: str):
    if role_id in _PROTECTED:
        raise HTTPException(status_code=400, detail="默认角色不能删除")
    if not zero_store.get_role(role_id):
        raise HTTPException(status_code=404, detail=f"角色不存在: {role_id}")
    zero_store.delete_role(role_id)
    return {"code": 0, "data": {"deleted": True, "id": role_id}}


@router.post("/roles/{role_id}/promote")
def promote_role(role_id: str):
    """临时角色 → 持久角色（用户手动保存）。"""
    current = _get_or_404(role_id)
    role = zero_store.upsert_role({
        "id": role_id,
        "group_id": current.get("group_id"),
        "name": current.get("name"),
        "persona": current.get("persona"),
        "expertise": current.get("expertise"),
        "avatar": current.get("avatar"),
        "envelope": current.get("envelope") or {},
        "model_code": current.get("model_code"),
        "scope": "persistent",
        "source": current.get("source") or "user",
        "session_id": "",   # 显式清空归属会话
    })
    return {"code": 0, "data": _public(role)}
