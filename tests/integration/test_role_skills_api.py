"""角色专属技能：私有可见性、20 个上限与合并。"""
from __future__ import annotations

API = "/api/agent-zero"


def _role(client, name):
    return client.post(f"{API}/roles", json={"name": name}).json()["data"]


def _skill(client, role_id, name):
    res = client.post(f"{API}/app/skills", json={
        "name": name, "description": f"{name} 描述", "content": f"# {name}\n步骤",
        "role_id": role_id,
    })
    assert res.status_code == 200, res.text
    return res.json()["data"]


def test_role_skills_are_private(client, monkeypatch, tmp_path):
    from services.agent_zero.services import skill_service

    monkeypatch.setattr(skill_service, "skills_root", lambda: tmp_path / "skills")
    a = _role(client, "角色 A")
    b = _role(client, "角色 B")
    _skill(client, a["id"], "A 的审阅技能")

    own = client.get(f"{API}/app/skills", params={
        "role_id": a["id"], "include_global": "false"}).json()["data"]["items"]
    other = client.get(f"{API}/app/skills", params={
        "role_id": b["id"], "include_global": "false"}).json()["data"]["items"]
    assert [s["name"] for s in own] == ["A 的审阅技能"]
    assert other == []

    assert skill_service.find_skill("A 的审阅技能", role_id=a["id"])
    assert skill_service.find_skill("A 的审阅技能", role_id=b["id"]) is None
    prompt_a = skill_service.skills_prompt(a["id"])
    prompt_b = skill_service.skills_prompt(b["id"])
    assert "A 的审阅技能" in prompt_a
    assert "A 的审阅技能" not in prompt_b

    # 删除角色时带走它的专属技能。
    assert client.delete(f"{API}/roles/{a['id']}").status_code == 200
    assert skill_service.list_skills(role_id=a["id"], include_global=False) == []


def test_role_skill_limit_and_merge(client, monkeypatch, tmp_path):
    from services.agent_zero.services import skill_service

    monkeypatch.setattr(skill_service, "skills_root", lambda: tmp_path / "skills")
    role = _role(client, "技能上限角色")
    made = [_skill(client, role["id"], f"技能{i}") for i in range(20)]

    over = client.post(f"{API}/app/skills", json={
        "name": "超出上限", "content": "# x", "role_id": role["id"],
    })
    assert over.status_code == 400
    assert "20" in over.json()["detail"]

    merged = client.post(f"{API}/app/skills/merge", json={
        "role_id": role["id"], "skill_ids": [made[0]["id"], made[1]["id"]],
        "name": "合并技能", "description": "合并后",
    })
    assert merged.status_code == 200, merged.text
    assert merged.json()["data"]["name"] == "合并技能"

    items = client.get(f"{API}/app/skills", params={
        "role_id": role["id"], "include_global": "false"}).json()["data"]["items"]
    assert len(items) == 19
    assert all(s["id"] not in {made[0]["id"], made[1]["id"]} for s in items)
    assert any(s["name"] == "合并技能" for s in items)


def test_role_skill_tools_use_role_scope(client, monkeypatch, tmp_path):
    from services.agent_zero.runtime.zero.dispatch import ToolDispatcher
    from services.agent_zero.services import skill_service

    monkeypatch.setattr(skill_service, "skills_root", lambda: tmp_path / "skills")
    role = _role(client, "工具技能角色")
    other = _role(client, "其他角色")
    _skill(client, role["id"], "工具私有技能")
    _skill(client, other["id"], "别处技能")

    import asyncio
    loop = ToolDispatcher()
    out = asyncio.run(loop._act_skills(
        "list_skills", {}, role_id=role["id"]))
    names = [s["name"] for s in out["skills"]]
    assert "工具私有技能" in names and "别处技能" not in names

    denied = asyncio.run(loop._act_skills(
        "use_skill", {"name": "别处技能"}, role_id=role["id"]))
    assert "error" in denied
