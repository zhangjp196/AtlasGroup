"""部门 API：多级组织树，角色按部门归档。

- `GET    /departments`          全量部门（扁平，带 depth / path / role_count）
- `POST   /departments`          新建（parent_id 可空 = 根部门）
- `PATCH  /departments/{id}`     改名 / 移动（禁止挂到自己或后代下）
- `DELETE /departments/{id}`     删除；子部门上移，部门内角色变「未安排」

角色是否属于部门由 `roles.department_id` 决定；为空即「未安排」。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.agent_zero.services import department_service

router = APIRouter()


class DepartmentCreateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    avatar: Optional[str] = Field(None, max_length=500)
    parent_id: Optional[str] = Field(None, max_length=80)
    sort: Optional[int] = None


class DepartmentUpdateBody(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    avatar: Optional[str] = Field(None, max_length=500)
    parent_id: Optional[str] = Field(None, max_length=80)
    sort: Optional[int] = None


@router.get("/departments")
def list_departments():
    return {"code": 0, "data": {"items": department_service.list_departments()}}


@router.post("/departments")
def create_department(body: DepartmentCreateBody):
    try:
        dept = department_service.create_department(
            body.name, parent_id=body.parent_id, sort=body.sort or 0,
            avatar=body.avatar or "")
    except department_service.DepartmentError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": dept}


@router.patch("/departments/{dept_id}")
def update_department(dept_id: str, body: DepartmentUpdateBody):
    fields = body.model_fields_set
    kwargs = {"name": body.name, "sort": body.sort}
    if "avatar" in fields:
        kwargs["avatar"] = "" if body.avatar is None else body.avatar
    if "parent_id" in fields:
        kwargs["parent_id"] = body.parent_id
    try:
        dept = department_service.update_department(dept_id, **kwargs)
    except department_service.DepartmentError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": dept}


@router.delete("/departments/{dept_id}")
def delete_department(dept_id: str):
    try:
        out = department_service.delete_department(dept_id)
    except department_service.DepartmentError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"code": 0, "data": out}
