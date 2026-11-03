"""GDAG 关系库存储。

纯关系底座（v1.1 决策：不用向量）：节点 / 边 / 执行状态 / path_view 全部
走 Postgres 邻接表 + JSONB 属性；执行状态节点级 CAS（JSONB 条件更新）。

热路径：任务子图读走单查询 path_view（write-through 由调用方缓存，
本层不引入额外缓存层）；写入逐条幂等（upsert 可重放）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from services.agent_zero.database import get_db_schema, get_session_local, retry_on_locked
from services.agent_zero.models.zero import GdagEdge, GdagNode  # noqa: F401

logger = logging.getLogger(__name__)

NODE_TYPES = (
    "entity", "fact", "relation", "task", "step",
    "artifact", "experience", "tool_call",
)
EDGE_EXEC_STATES = ("pending", "running", "done", "failed")


class GdagError(Exception):
    """GDAG 存储异常。"""


@dataclass
class PathView:
    """任务子图单查询视图：节点 + 边 + 邻接表（上下文装配直接消费）。"""

    task_id: str
    root: Optional[str] = None
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    edges: List[Dict[str, Any]] = field(default_factory=list)
    adjacency: Dict[str, List[str]] = field(default_factory=dict)  # from_id -> [to_id]

    def node(self, node_id: str) -> Optional[Dict[str, Any]]:
        for n in self.nodes:
            if n["id"] == node_id:
                return n
        return None

    def ready_nodes(self, done_states=("done",)) -> List[Dict[str, Any]]:
        """就绪执行节点：自身 pending 且前驱全部 done（或 failed 且已 re-planned）。

        - 只收 step 节点（根 task 节点不是执行单元）；
        - 排除 superseded（DAG 重规划后的旧节点）；
        - 与 ZeroLoop._ready 同一语义（此处为单一事实来源，loop 委托本方法）。
        """
        preds: Dict[str, List[str]] = {}
        for e in self.edges:
            preds.setdefault(e["to_id"], []).append(e["from_id"])
        by_id = {n["id"]: n for n in self.nodes}

        def _pred_ok(pid: str) -> bool:
            p = by_id.get(pid)
            if p is None:
                return True  # 图外前驱（跨任务引用）不阻塞
            if p.get("type") != "step":
                return True  # 根 task 节点等非执行节点不阻塞首层 step
            props = p.get("props") or {}
            st = props.get("exec_state")
            return st == "done" or (st == "failed" and bool(props.get("re-planned")))

        out = []
        for n in self.nodes:
            if n.get("type") != "step":
                continue
            props = n.get("props") or {}
            if props.get("superseded"):
                continue
            if props.get("exec_state") not in (None, "pending"):
                continue
            if all(_pred_ok(p) for p in preds.get(n["id"], [])):
                out.append(n)
        return out


def _schema() -> str:
    return get_db_schema()


def _q(table: str) -> str:
    s = _schema()
    return f'"{s}"."{table}"' if s and s != "public" else f'"{table}"'


class GdagStore:
    """GDAG 存储（会话即连接：调用方管理 db 生命周期，方法内独立开短会话）。"""

    # ── 节点 / 边 ────────────────────────────────────────────

    def upsert_node(self, node: Dict[str, Any]) -> None:
        """幂等 upsert 节点（id / type / camp / props / confidence / sources）。"""
        nid = str(node.get("id") or "").strip()
        ntype = str(node.get("type") or "").strip()
        camp = str(node.get("camp") or "").strip()
        if not nid or ntype not in NODE_TYPES or not camp:
            raise GdagError(f"非法节点: id={nid!r} type={ntype!r} camp={camp!r}")
        props = node.get("props") or {}
        if not isinstance(props, dict):
            raise GdagError("props 必须为 dict")
        conf = node.get("confidence")
        sources = node.get("sources")

        SessionLocal = get_session_local()

        def _write() -> None:
            with SessionLocal() as db:
                db.execute(
                    text(f"INSERT INTO {_q('gdag_nodes')} "
                         f"(id, type, camp, props, confidence, sources) "
                         f"VALUES (:id, :type, :camp, :props, :confidence, :sources) "
                         f"ON CONFLICT (id) DO UPDATE SET props = EXCLUDED.props, "
                         f"confidence = EXCLUDED.confidence, sources = EXCLUDED.sources"),
                    {"id": nid, "type": ntype, "camp": camp,
                     "props": json.dumps(props, ensure_ascii=False),
                     "confidence": conf,
                     "sources": json.dumps(sources, ensure_ascii=False)
                     if sources is not None else None},
                )
                db.commit()

        retry_on_locked(_write)

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        SessionLocal = get_session_local()
        with SessionLocal() as db:
            row = db.execute(
                text(f'SELECT id, type, camp, props, confidence, sources, created_at '
                     f'FROM {_q("gdag_nodes")} WHERE id = :id'),
                {"id": node_id},
            ).mappings().first()
        if not row:
            return None
        from services.agent_zero.stores.zero_store import decode_row

        data = decode_row(row)
        return {
            "id": data["id"], "type": data["type"], "camp": data["camp"],
            "props": data["props"] or {}, "confidence": data["confidence"],
            "sources": data["sources"], "created_at": str(data["created_at"]),
        }

    def delete_node(self, node_id: str) -> bool:
        """删除节点及其关联边（记忆「遗忘」路径）。返回是否删到了。"""
        nid = str(node_id or "").strip()
        if not nid:
            return False
        SessionLocal = get_session_local()
        with SessionLocal() as db:
            # 先删边再删节点：gdag_edges 有外键指向 gdag_nodes，顺序不能颠倒
            db.execute(
                text(f"DELETE FROM {_q('gdag_edges')} "
                    f"WHERE from_id = :id OR to_id = :id"),
                {"id": nid},
            )
            r = db.execute(
                text(f"DELETE FROM {_q('gdag_nodes')} WHERE id = :id"),
                {"id": nid},
            )
            db.commit()
        return r.rowcount > 0

    def add_edge(self, edge: Dict[str, Any]) -> None:
        """幂等 upsert 边（from_id / to_id / kind 复合主键；weight / exec_state）。"""
        fid = str(edge.get("from_id") or "").strip()
        tid = str(edge.get("to_id") or "").strip()
        kind = str(edge.get("kind") or "").strip()
        if not fid or not tid or not kind:
            raise GdagError(f"非法边: {fid!r} -> {tid!r} kind={kind!r}")
        weight = edge.get("weight")
        exec_state = edge.get("exec_state")
        if exec_state is not None and exec_state not in EDGE_EXEC_STATES:
            raise GdagError(f"非法边执行状态: {exec_state!r}")

        SessionLocal = get_session_local()
        with SessionLocal() as db:
            db.execute(
                text(f"INSERT INTO {_q('gdag_edges')} "
                     f"(from_id, to_id, kind, weight, exec_state) "
                     f"VALUES (:f, :t, :k, :w, :s) "
                     f"ON CONFLICT (from_id, to_id, kind) DO NOTHING"),
                {"f": fid, "t": tid, "k": kind, "w": weight, "s": exec_state},
            )
            db.commit()

    def get_edges(self, node_id: Optional[str] = None,
                  kinds: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """读取边（可按节点 + 边类型过滤）。"""
        sql = (f'SELECT from_id, to_id, kind, weight, exec_state FROM {_q("gdag_edges")}')
        conds, params = [], {}
        if node_id:
            conds.append("(from_id = :nid OR to_id = :nid)")
            params["nid"] = node_id
        if kinds:
            marks = ",".join(f":k{i}" for i in range(len(kinds)))
            conds.append(f"kind IN ({marks})")
            params.update({f"k{i}": k for i, k in enumerate(kinds)})
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        SessionLocal = get_session_local()
        with SessionLocal() as db:
            rows = db.execute(text(sql), params).mappings().all()
        return [
            {"from_id": r["from_id"], "to_id": r["to_id"], "kind": r["kind"],
             "weight": r["weight"], "exec_state": r["exec_state"]}
            for r in rows
        ]

    # ── 执行状态（CAS）──────────────────────────────────────

    def set_exec_state(self, node_id: str, state: str,
                       expected: Optional[str] = None) -> bool:
        """节点执行状态 CAS 更新。

        - expected=None：无条件置位（用于失败标记 / running 重置）；
        - expected=<state>：仅当前态相符才更新（CAS 防并发双跑）。
        返回 False = 前态不符（并发竞争），调用方按失败阶梯处理。
        """
        if state not in EDGE_EXEC_STATES:
            raise GdagError(f"非法执行状态: {state!r}")
        SessionLocal = get_session_local()
        # 注意：不可写 `:state::text`（`::` 会破坏 SQLAlchemy 绑定参数解析），
        # 用 CAST(:state AS text)。
        _set = "props = json_set(props, '$.exec_state', :state)"
        with SessionLocal() as db:
            if expected is None:
                # 无条件置位（用于失败标记 / 重置）：current_set 语义
                sql = f"UPDATE {_q('gdag_nodes')} SET {_set} WHERE id = :id"
                params = {"id": node_id, "state": state}
            else:
                sql = (
                    f"UPDATE {_q('gdag_nodes')} SET {_set} "
                    f"WHERE id = :id AND props->>'exec_state' = :exp"
                )
                params = {"id": node_id, "state": state, "exp": expected}
            res = db.execute(text(sql), params)
            db.commit()
            return (res.rowcount or 0) > 0

    def set_edge_exec_state(self, from_id: str, to_id: str, kind: str, state: str) -> None:
        SessionLocal = get_session_local()
        with SessionLocal() as db:
            db.execute(
                text(f"UPDATE {_q('gdag_edges')} SET exec_state = :s "
                     f"WHERE from_id = :f AND to_id = :t AND kind = :k"),
                {"s": state, "f": from_id, "t": to_id, "k": kind},
            )
            db.commit()

    # ── 图查询 ──────────────────────────────────────────────

    def task_node(self, task_id: str) -> Optional[Dict[str, Any]]:
        """任务的 GDAG 根节点（type='task' 且 props.task_id 匹配，取最新）。"""
        SessionLocal = get_session_local()
        with SessionLocal() as db:
            row = db.execute(
                text(f"SELECT id, type, camp, props, confidence, sources, created_at "
                     f"FROM {_q('gdag_nodes')} "
                     f"WHERE type = 'task' AND props->>'task_id' = :tid "
                     f"ORDER BY created_at DESC LIMIT 1"),
                {"tid": task_id},
            ).mappings().first()
        if not row:
            return None
        from services.agent_zero.stores.zero_store import decode_row

        data = decode_row(row)
        return {
            "id": data["id"], "type": data["type"], "camp": data["camp"],
            "props": data["props"] or {}, "confidence": data["confidence"],
            "sources": data["sources"], "created_at": str(data["created_at"]),
        }

    def path_view(self, task_id: str) -> PathView:
        """任务子图单查询：根节点**递归闭包**收集全部可达节点 + 边（预聚合 + 邻接表）。

        注意：必须用递归 CTE 取传递闭包——早期实现只取根的两跳邻域，
        线性链 DAG（root→n1→n2→n3…）会被截断，导致深层节点不参与调度、
        任务提前判完成（线上缺陷）。
        """
        root = self.task_node(task_id)
        if not root:
            return PathView(task_id=task_id)
        SessionLocal = get_session_local()
        with SessionLocal() as db:
            rows = db.execute(
                text(
                    # 单递归项（PG 限制）：沿边双向扩张，UNION 去重亦防环
                    f"WITH RECURSIVE reachable(id) AS ( "
                    f"  SELECT CAST(:root AS text) "
                    f"  UNION "
                    f"  SELECT CASE WHEN e.from_id = r.id THEN e.to_id ELSE e.from_id END "
                    f"  FROM {_q('gdag_edges')} e "
                    f"  JOIN reachable r ON (e.from_id = r.id OR e.to_id = r.id) "
                    f") "
                    f"SELECT from_id, to_id, kind FROM {_q('gdag_edges')} "
                    f"WHERE from_id IN (SELECT id FROM reachable) "
                    f"   OR to_id IN (SELECT id FROM reachable)"
                ),
                {"root": root["id"]},
            ).mappings().all()
            node_ids = {root["id"]}
            for e in rows:
                node_ids.add(e["from_id"])
                node_ids.add(e["to_id"])
            marks = ",".join(f":n{i}" for i in range(len(node_ids)))
            params = {f"n{i}": nid for i, nid in enumerate(node_ids)}
            nodes = db.execute(
                text(f"SELECT id, type, camp, props, confidence, sources, created_at "
                     f"FROM {_q('gdag_nodes')} WHERE id IN ({marks})"),
                params,
            ).mappings().all()
        from services.agent_zero.stores.zero_store import decode_row

        node_dicts = []
        for r in nodes:
            d = decode_row(r)
            node_dicts.append({
                "id": d["id"], "type": d["type"], "camp": d["camp"],
                "props": d["props"] or {}, "confidence": d["confidence"],
                "sources": d["sources"], "created_at": str(d["created_at"]),
            })
        edge_dicts = [
            {"from_id": e["from_id"], "to_id": e["to_id"], "kind": e["kind"]}
            for e in rows
        ]
        adj: Dict[str, List[str]] = {}
        for e in edge_dicts:
            adj.setdefault(e["from_id"], []).append(e["to_id"])
        return PathView(
            task_id=task_id, root=root["id"],
            nodes=node_dicts, edges=edge_dicts, adjacency=adj,
        )

    def neighbors(self, node_id: str, kind: Optional[str] = None,
                  direction: str = "out") -> List[Dict[str, Any]]:
        """邻接节点（out=出边指向 / in=入边来源；kind 过滤边类型）。"""
        edges = self.get_edges(node_id, kinds=(kind,) if kind else None)
        want = {"out": "to_id", "in": "from_id"}[direction]
        ids = [e[want] for e in edges if e[want] != node_id]
        out = []
        for nid in dict.fromkeys(ids):
            n = self.get_node(nid)
            if n:
                out.append(n)
        return out

    def search(self, camp: str, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """规划 / 经验检索：纯关系（camp 硬过滤 + 关键词/结构评分，不用向量）。

        委托 retrieve.scored_search；返回 [{node, score, reasons}]。
        """
        from services.agent_zero.runtime.zero.gdag.retrieve import scored_search

        return scored_search(self, camp, query, top_k=top_k)
