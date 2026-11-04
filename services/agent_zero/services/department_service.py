"""部门（多级组织）的增删改查与校验。

界面路由与模型工具（`department_manage`）共用这一份实现，避免两边规则漂移：
- `list` / `create` / `update` / `delete`；
- 部门可多级，`parent_id` 为空表示根部门；
- 移动时禁止挂到自己或自己的后代下；
- 删除时子部门上移，部门内角色变成「未安排」。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.agent_zero.stores import zero_store

UNSET = object()


class DepartmentError(ValueError):
    """部门参数不合法（路由转 400，模型工具转 error）。"""


def _depth(parent_id: Optional[str], by_id: Dict[str, Dict[str, Any]]) -> int:
    seen = set()
    depth, cur = 0, str(parent_id or "")
    while cur and cur in by_id and cur not in seen:
        seen.add(cur)
        depth += 1
        cur = str(by_id[cur].get("parent_id") or "")
    return depth


def _descendants(dept_id: str, rows: List[Dict[str, Any]]) -> set:
    out, changed = {str(dept_id)}, True
    while changed:
        changed = False
        for row in rows:
            rid = str(row.get("id") or "")
            if rid in out:
                continue
            if str(row.get("parent_id") or "") in out:
                out.add(rid)
                changed = True
    return out


def _public(dept: Dict[str, Any], by_id: Dict[str, Dict[str, Any]],
            paths: Dict[str, str], counts: Dict[str, int]) -> Dict[str, Any]:
    dept_id = str(dept.get("id") or "")
    return {
        "id": dept_id,
        "name": str(dept.get("name") or ""),
        "avatar": str(dept.get("avatar") or ""),
        "parent_id": str(dept.get("parent_id") or ""),
        "sort": int(dept.get("sort") or 0),
        "depth": _depth(dept.get("parent_id"), by_id),
        "path": paths.get(dept_id, ""),
        "role_count": counts.get(dept_id, 0),
        "created_at": str(dept.get("created_at") or ""),
    }


def _snapshot() -> tuple:
    rows = zero_store.list_departments()
    by_id = {str(r.get("id") or ""): r for r in rows}
    return rows, by_id, zero_store.department_paths(), zero_store.role_counts_by_department()


def _public_one(dept: Dict[str, Any]) -> Dict[str, Any]:
    rows, by_id, paths, counts = _snapshot()
    return _public(dept, by_id, paths, counts)


def list_departments() -> List[Dict[str, Any]]:
    rows, by_id, paths, counts = _snapshot()
    return [_public(r, by_id, paths, counts) for r in rows]


def get_department(dept_id: str) -> Optional[Dict[str, Any]]:
    dept = zero_store.get_department(str(dept_id))
    return _public_one(dept) if dept else None


def create_department(name: str, parent_id: Optional[str] = None,
                      sort: int = 0, avatar: str = "") -> Dict[str, Any]:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise DepartmentError("部门名称不能为空")
    parent = str(parent_id or "").strip() or None
    if parent and not zero_store.get_department(parent):
        raise DepartmentError(f"上级部门不存在: {parent}")
    dept = zero_store.upsert_department({
        "id": zero_store.new_id("dept"),
        "name": clean_name,
        "avatar": avatar or "",
        "parent_id": parent,
        "sort": int(sort or 0),
    })
    return _public_one(dept)


def update_department(dept_id: str, *, name: Optional[str] = None,
                      avatar: Any = UNSET, parent_id: Any = UNSET,
                      sort: Optional[int] = None) -> Dict[str, Any]:
    current = zero_store.get_department(str(dept_id))
    if not current:
        raise DepartmentError(f"部门不存在: {dept_id}")
    if name is not None and not str(name).strip():
        raise DepartmentError("部门名称不能为空")

    parent = current.get("parent_id")
    if parent_id is not UNSET:
        parent = str(parent_id or "").strip() or None
        if parent:
            if parent == dept_id:
                raise DepartmentError("部门不能挂到自己下")
            rows, _by_id, _paths, _counts = _snapshot()
            if parent in _descendants(dept_id, rows):
                raise DepartmentError("部门不能挂到自己的子部门下")
            if not zero_store.get_department(parent):
                raise DepartmentError(f"上级部门不存在: {parent}")

    dept = zero_store.upsert_department({
        "id": dept_id,
        "name": (str(name).strip() if name is not None else current.get("name")),
        "avatar": (avatar if avatar is not UNSET else None),
        "parent_id": parent,
        "sort": (int(sort) if sort is not None else current.get("sort")),
    })
    return _public_one(dept)


def delete_department(dept_id: str) -> Dict[str, Any]:
    if not zero_store.get_department(str(dept_id)):
        raise DepartmentError(f"部门不存在: {dept_id}")
    return zero_store.delete_department(str(dept_id))
