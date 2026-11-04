"""部门 API：多级组织、角色归档、身份信息（名称 / 岗位 / 部门）。"""
from __future__ import annotations

API = "/api/agent-zero"


def _dept(client, name, parent_id=None):
    res = client.post(f"{API}/departments",
                      json={"name": name, "parent_id": parent_id})
    assert res.status_code == 200, res.text
    return res.json()["data"]


def test_department_tree_and_paths(client):
    root = _dept(client, "公司")
    product = _dept(client, "产品部", root["id"])
    design = _dept(client, "设计组", product["id"])

    items = client.get(f"{API}/departments").json()["data"]["items"]
    by_id = {d["id"]: d for d in items}
    assert by_id[root["id"]]["depth"] == 0
    assert by_id[product["id"]]["depth"] == 1
    assert by_id[design["id"]]["depth"] == 2
    assert by_id[design["id"]]["path"] == "公司 / 产品部 / 设计组"


def test_role_title_and_department(client):
    dept = _dept(client, "研发部")
    role = client.post(f"{API}/roles", json={
        "name": "张三", "title": "后端工程师", "department_id": dept["id"],
        "expertise": "API 与数据",
    }).json()["data"]
    assert role["name"] == "张三"
    assert role["title"] == "后端工程师"
    assert role["department_id"] == dept["id"]
    assert role["department"] == "研发部"

    patched = client.patch(f"{API}/roles/{role['id']}", json={
        "title": "技术负责人",
    }).json()["data"]
    assert patched["title"] == "技术负责人"
    assert patched["department_id"] == dept["id"]

    # 显式传 null = 移出部门（未安排），岗位不受影响。
    cleared = client.patch(f"{API}/roles/{role['id']}", json={
        "department_id": None,
    }).json()["data"]
    assert cleared["department_id"] == ""
    assert cleared["department"] == ""
    assert cleared["title"] == "技术负责人"


def test_department_moves_and_cycle_guard(client):
    root = _dept(client, "总部")
    a = _dept(client, "A 部", root["id"])
    b = _dept(client, "B 组", a["id"])

    bad = client.patch(f"{API}/departments/{root['id']}", json={"parent_id": b["id"]})
    assert bad.status_code == 400
    self_ref = client.patch(f"{API}/departments/{a['id']}", json={"parent_id": a["id"]})
    assert self_ref.status_code == 400

    moved = client.patch(f"{API}/departments/{b['id']}", json={"parent_id": root["id"]})
    assert moved.status_code == 200, moved.text
    assert moved.json()["data"]["parent_id"] == root["id"]


def test_delete_department_moves_children_and_unassigns_roles(client):
    root = _dept(client, "中心")
    a = _dept(client, "甲部", root["id"])
    child = _dept(client, "甲部一组", a["id"])
    role = client.post(f"{API}/roles", json={
        "name": "李四", "title": "测试", "department_id": a["id"],
    }).json()["data"]

    out = client.delete(f"{API}/departments/{a['id']}").json()["data"]
    assert out["deleted"] is True
    assert out["children_moved"] == 1
    assert out["roles_unassigned"] == 1

    items = client.get(f"{API}/departments").json()["data"]["items"]
    moved = next(d for d in items if d["id"] == child["id"])
    assert moved["parent_id"] == root["id"]
    role_after = next(r for r in client.get(f"{API}/roles").json()["data"]["items"]
                      if r["id"] == role["id"])
    assert role_after["department_id"] == ""


def test_department_manage_tool(client):
    """模型工具也能新建 / 调整部门（Superagent 与 plan 包共用）。"""
    from services.agent_zero.task_layer import conversation
    from services.agent_zero.tools import packs, registry

    assert registry.get_tool("department_manage") is not None
    plan = next(p for p in packs.PACKS if p.code == "plan")
    assert plan.ops["department_admin"] == "department_manage"

    root = conversation._exec_department_manage({
        "action": "create", "name": "通过工具建的部门"})
    assert root.get("ok"), root
    root = root["department"]
    child = conversation._exec_department_manage({
        "action": "create", "name": "子部门", "parent_id": root["id"]})
    assert child.get("ok"), child
    child = child["department"]

    listed = conversation._exec_department_manage({"action": "list"})
    assert any(d["id"] == child["id"] for d in listed["departments"])

    moved = conversation._exec_department_manage({
        "action": "update", "department_id": child["id"], "name": "改名后的子部门"})
    assert moved["department"]["name"] == "改名后的子部门"

    bad = conversation._exec_department_manage({
        "action": "update", "department_id": root["id"],
        "parent_id": child["id"]})
    assert bad.get("error")

    role = conversation._exec_role_manage({}, {
        "action": "create", "name": "工具角色", "title": "岗位A",
        "department_id": root["id"], "scope": "persistent"})
    assert role["role"]["department_id"] == root["id"]
    assert role["role"]["department"] == "通过工具建的部门"

    deleted = conversation._exec_department_manage({
        "action": "delete", "department_id": child["id"]})
    assert deleted.get("ok")
    assert conversation._exec_department_manage(
        {"action": "list"})["departments"][0]["id"] == root["id"]


def test_unknown_department_rejected(client):
    res = client.post(f"{API}/roles", json={
        "name": "无部门", "department_id": "dept-missing",
    })
    assert res.status_code == 400
    bad_child = client.post(f"{API}/departments", json={
        "name": "子部门", "parent_id": "dept-missing",
    })
    assert bad_child.status_code == 400
