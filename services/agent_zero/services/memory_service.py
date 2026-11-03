"""类型化记忆层（GDAG 之上的记忆 API）。

为什么需要这一层（对标业界 vibecoding 工具的记忆机制）：

- Claude Code：CLAUDE.md 项目约定 + 会话间自动记忆 —— 约定文件自动注入上下文
- Cline Memory Bank：结构化记忆（projectBrief / activeContext / decisions）
- Graphiti / Zep：实体 + 关系 + 时间衰减的上下文图谱，检索按"相关性 × 新鲜度"
- Aider：每轮构建 repo map（符号级本体）作为检索式上下文

AtlasX 已有 GDAG（节点/边 + 关键词/结构/新鲜度评分检索），但此前只有裸的
memory_write / memory_read：记忆没有类型、没有作用域，也**不会自动进入上下文**
——只有模型主动调用工具时才看得见。这一层补上三件事：

1. **类型与作用域**：fact / decision / preference / experience，按工作区分 camp；
2. **自动沉淀**：每轮 run 结束自动写入一条 experience（目标 / 工具 / 产物 / 结论），
   无需模型自觉；
3. **自动检索注入**：每次对话开始时按用户消息召回相关记忆，注入系统提示。

检索沿用 gdag.retrieve.scored_search（关键词 + 结构 + 新鲜度 + 置信度），
本层只是把"记忆"的语义与作用域约定固化下来。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from services.agent_zero.database import get_db_schema
from services.agent_zero.runtime.zero.gdag import retrieve
from services.agent_zero.runtime.zero.gdag.store import GdagStore
from services.agent_zero.stores import zero_store

logger = logging.getLogger(__name__)

# 记忆类型（对外语义） → GDAG 节点类型（检索结构权重在 retrieve._TYPE_WEIGHT）
MEMORY_KINDS = ("fact", "decision", "preference", "experience")
_KIND_TO_NODE_TYPE = {
    "fact": "fact",
    "decision": "fact",
    "preference": "fact",
    "experience": "experience",
}

MAX_CONTENT_CHARS = 4000
# ── 合并 / 剔除（记忆整理）──────────────────────────────────
# 写入时：同一 camp + 同一 kind 的旧记忆里找「完全相同」或「近重复」，
# 命中则并入原节点而不是新增一条——记忆只增不减会让召回越来越吵。
# 整理时（后台 / 手动）：合并历史重复项、归档低价值项、单 camp 超上限淘汰。
MERGE_SIMILARITY = 0.82       # 通用近重复阈值（fact / decision / preference / 手写经验）
EXPERIENCE_SIMILARITY = 0.70  # 自动沉淀的 run 经验：同一目标反复执行很常见，阈值放宽
MERGE_SCAN_LIMIT = 400        # 写入时最多回看多少条同 kind 记忆
MERGE_COMPARE_CHARS = 800     # 相似度比较只看前 N 个字符（长文尾部差异不影响判定）
ARCHIVE_AFTER_DAYS = 90       # 自动/蒸馏记忆：多少天没被召回且置信度不高 → 归档
LOW_CONFIDENCE = 0.4          # 低于该置信度的自动记忆，30 天后归档
LOW_CONF_DAYS = 30
CAMP_MAX_MEMORIES = 300       # 单 camp 活跃记忆上限（超出按价值归档，仅动 auto/llm）
CONSOLIDATE_MIN_INTERVAL = 600  # 同一 camp 后台整理的最小间隔（秒）

_TS_RE = re.compile(r"\[\d{4}-\d{2}-\d{2}[T ][^\]]*\]")
_PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def _norm_text(text: str) -> str:
    """规范化正文：去时间戳（每次 run 都不同）、空白、标点，转小写。"""
    s = _TS_RE.sub(" ", str(text or ""))
    s = _PUNCT_RE.sub("", s.lower())
    return s


def _fingerprint(content: str) -> str:
    return hashlib.sha1(_norm_text(content).encode("utf-8")).hexdigest()


def _similarity(a: str, b: str, cap: int = MERGE_COMPARE_CHARS,
                threshold: float = MERGE_SIMILARITY) -> float:
    """规范化正文的相似度；先用 quick_ratio 挡掉不相似的，避免全量比对太慢。"""
    a, b = a[:cap], b[:cap]
    if not a or not b:
        return 0.0
    sm = SequenceMatcher(None, a, b)
    if sm.quick_ratio() < threshold - 0.03:
        return 0.0
    return sm.ratio()


def _dup_threshold(kind: str, source_a: Any, source_b: Any) -> float:
    """近重复阈值：自动沉淀的 run 经验放宽（重复执行是常态），手写记忆从严。"""
    if str(kind) == "experience" and str(source_a or "") in ("auto", "llm") \
            and str(source_b or "") in ("auto", "llm"):
        return EXPERIENCE_SIMILARITY
    return MERGE_SIMILARITY


def _is_duplicate(a: str, b: str, threshold: float = MERGE_SIMILARITY) -> bool:
    """近重复判定：相似度达标，或短的一条完全被长的一条包含（短文本定长差异大）。

    例：「经验：call_team 嵌套最多 2 层。」是「…最多 2 层，超出会报…」的前缀，
    按字符相似度可能只有 0.72，但语义上就是同一条记忆。
    """
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 6 and short in long:
        return True
    return _similarity(a, b, threshold=threshold) >= threshold


_store = GdagStore()


def camp_for(workspace_id: Optional[str]) -> str:
    """记忆作用域：工作区独立 camp；无工作区落到全局 default。"""
    ws = str(workspace_id or "").strip()
    return ws or "default"


def remember(content: str, *, kind: str = "fact",
             workspace_id: Optional[str] = None,
             conversation_id: Optional[str] = None,
             tags: Optional[List[str]] = None,
             entities: Optional[List[str]] = None,
             confidence: float = 0.7,
             source: str = "manual") -> Dict[str, Any]:
    """写入一条记忆，并挂到代码本体上（about 边）。返回 {ok, node_id}。

    本体结合的关键：记忆不是孤立文本——它锚定到实体（文件 / 符号 /
    概念节点）。显式传 entities，或从正文自动识别提及的文件 / 符号；
    后续检索「gen_novels.py 相关的一切」时，记忆通过二跳被召回。
    """
    content = str(content or "").strip()
    if not content:
        raise ValueError("记忆内容为空")
    kind = kind if kind in MEMORY_KINDS else "fact"
    # 先做写入时合并：完全相同 / 近重复 → 并入原节点，不新增
    dup = _find_duplicate(content, kind, workspace_id, source)
    if dup is not None:
        return _merge_into(dup, content, kind=kind, workspace_id=workspace_id,
                           conversation_id=conversation_id, tags=tags,
                           entities=entities, confidence=confidence, source=source)
    nid = zero_store.new_id("mem")
    _store.upsert_node({
        "id": nid,
        "type": _KIND_TO_NODE_TYPE[kind],
        "camp": camp_for(workspace_id),
        "props": {
            "title": content[:80],
            "content": content[:MAX_CONTENT_CHARS],
            "kind": "memory",
            "memory_type": kind,
            "tags": [str(t) for t in (tags or []) if str(t).strip()][:8],
            "workspace_id": workspace_id,
            "conversation_id": conversation_id,
            "source": source,
            "captured_at": time.time(),
            "fingerprint": _fingerprint(content),
            "hits": 0,
        },
        "confidence": max(0.0, min(1.0, float(confidence))),
    })
    # ── 挂到本体：显式实体 + 正文自动识别的实体 ──
    ent_ids: set = set()
    if workspace_id:
        try:
            from services.agent_zero.services import codemap

            ent_ids.update(codemap.resolve_entities(workspace_id, entities or []))
            ent_ids.update(codemap.mentioned_entities(content, workspace_id))
        except Exception as e:
            logger.debug("记忆实体挂接失败（不影响记忆本身）: %s", e)
    for eid in ent_ids:
        try:
            _store.add_edge({"from_id": nid, "to_id": eid, "kind": "about"})
        except Exception:
            pass
    return {"ok": True, "node_id": nid, "linked_entities": sorted(ent_ids)}


def _find_duplicate(content: str, kind: str,
                    workspace_id: Optional[str],
                    source: str = "manual") -> Optional[Dict[str, Any]]:
    """在同一 camp、同一 kind 的活跃记忆里找完全相同或近重复的一条。

    完全相同（规范化后指纹一致）直接命中；否则按相似度找最像的一条，
    达到 MERGE_SIMILARITY 才视为重复——阈值偏高，避免把「同类但不同事」
    的经验错误合并。
    """
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import decode_row

    camp = camp_for(workspace_id)
    fp = _fingerprint(content)
    norm_new = _norm_text(content)
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id, type, camp, props, confidence, sources, created_at "
                 f"FROM {_q('gdag_nodes')} WHERE camp = :camp "
                 f"AND json_extract(props, '$.kind') = 'memory' "
                 f"AND COALESCE(json_extract(props, '$.archived'), 0) = 0 "
                 f"AND json_extract(props, '$.memory_type') = :kind "
                 f"ORDER BY created_at DESC LIMIT :n"),
            {"camp": camp, "kind": kind, "n": MERGE_SCAN_LIMIT},
        ).mappings().all()

    best: Optional[Dict[str, Any]] = None
    for r in rows:
        d = decode_row(r)
        props = d.get("props") or {}
        if props.get("fingerprint") == fp:
            d["_merge"] = "exact"
            return d
        old_norm = _norm_text(props.get("content") or "")
        if old_norm == norm_new:
            d["_merge"] = "exact"
            return d
        # 长度差过大不可能近重复，省掉一次比对
        if abs(len(old_norm) - len(norm_new)) > max(len(norm_new), 200):
            continue
        thr = _dup_threshold(kind, source, props.get("source"))
        if _is_duplicate(norm_new, old_norm, thr):
            best = d
            break
    if best is not None:
        best["_merge"] = "similar"
    return best


def _merge_into(old: Dict[str, Any], content: str, *, kind: str,
                workspace_id: Optional[str],
                conversation_id: Optional[str],
                tags: Optional[List[str]], entities: Optional[List[str]],
                confidence: float, source: str) -> Dict[str, Any]:
    """把新记忆并入旧节点：内容取更完整的一版，标签/实体取并集，置信度取最大。"""
    old_id = old["id"]
    old_props = dict(old.get("props") or {})
    old_content = str(old_props.get("content") or "")
    new_norm, old_norm = _norm_text(content), _norm_text(old_content)

    # 新内容长度不低于旧内容 90% 时采用新内容（最新结论优先），否则保留更完整的旧内容
    if len(new_norm) >= int(len(old_norm) * 0.9):
        merged_content = content[:MAX_CONTENT_CHARS]
        merged_conv = conversation_id or old_props.get("conversation_id")
    else:
        merged_content = old_content[:MAX_CONTENT_CHARS]
        merged_conv = old_props.get("conversation_id")

    merged_tags = list(dict.fromkeys(
        [str(t) for t in (old_props.get("tags") or [])]
        + [str(t) for t in (tags or []) if str(t).strip()]))[:8]
    merged_source = "manual" if "manual" in (str(old_props.get("source") or ""), source) else (
        str(old_props.get("source") or source))

    props = {
        **old_props,
        "title": merged_content[:80],
        "content": merged_content,
        "tags": merged_tags,
        "conversation_id": merged_conv,
        "source": merged_source,
        "workspace_id": old_props.get("workspace_id") or workspace_id,
        "fingerprint": _fingerprint(merged_content),
        "merged_count": int(old_props.get("merged_count") or 0) + 1,
        "last_merged_at": time.time(),
        "hits": int(old_props.get("hits") or 0),
    }
    _store.upsert_node({
        "id": old_id,
        "type": old.get("type") or _KIND_TO_NODE_TYPE[kind],
        "camp": old.get("camp") or camp_for(workspace_id),
        "props": props,
        "confidence": max(float(old.get("confidence") or 0.0),
                          float(confidence or 0.0)),
        "sources": old.get("sources"),
    })

    ent_ids: set = set()
    ws = props.get("workspace_id")
    if ws:
        try:
            from services.agent_zero.services import codemap

            ent_ids.update(codemap.resolve_entities(ws, entities or []))
            ent_ids.update(codemap.mentioned_entities(merged_content, ws))
        except Exception as e:
            logger.debug("记忆实体挂接失败（合并时，不影响记忆）: %s", e)
    for eid in ent_ids:
        try:
            _store.add_edge({"from_id": old_id, "to_id": eid, "kind": "about"})
        except Exception:
            pass
    return {"ok": True, "node_id": old_id, "merged": old.get("_merge") or "similar",
            "merged_count": props["merged_count"],
            "linked_entities": sorted(ent_ids)}


def recall(query: str, *, workspace_id: Optional[str] = None,
           limit: int = 6) -> List[Dict[str, Any]]:
    """按相关性召回记忆：本工作区 + 全局，合并排序。

    两条路径，结果合并去重：
      1. 关键词路径：scored_search（关键词 + 结构 + 新鲜度 + 置信度）
      2. **本体锚定路径**：query 先匹配代码实体（文件 / 符号），再沿
         `about` 边二跳拿到挂接的记忆——即使记忆正文没提 query 的词。
         这正是「本体论 × 记忆」的结合：实体是索引，记忆挂在实体上。

    返回 [{id, type, memory_type, content, tags, score, linked_entities}]。
    """
    merged: Dict[str, Dict[str, Any]] = {}

    camps = [camp_for(workspace_id)]
    if camps[0] != "default":
        camps.append("default")
    for camp in camps:
        try:
            hits = retrieve.scored_search(_store, camp, query or "",
                                         top_k=max(1, min(int(limit), 20)),
                                         types=("fact", "experience", "entity"))
        except Exception as e:
            logger.debug("记忆检索失败 camp=%s: %s", camp, e)
            continue
        for h in hits:
            props = h["node"].get("props") or {}
            if props.get("kind") != "memory":
                continue
            if props.get("archived"):
                continue   # 已归档（合并/淘汰）不再进入召回
            m = {
                "id": h["node"]["id"],
                "type": h["node"]["type"],
                "memory_type": props.get("memory_type") or "fact",
                "content": str(props.get("content") or ""),
                "tags": props.get("tags") or [],
                "workspace_id": props.get("workspace_id"),
                "source": props.get("source") or "manual",
                "score": h.get("score", 0),
                "linked_entities": [],
            }
            prev = merged.get(m["id"])
            if prev is None or m["score"] > prev["score"]:
                merged[m["id"]] = m

    for m in _anchored_memories(query, workspace_id):
        prev = merged.get(m["id"])
        if prev is None or m["score"] > prev["score"]:
            merged[m["id"]] = m

    # 关键词路径的记忆也补上实体挂接（本体可见性）
    ids = list(merged)
    if ids:
        try:
            from sqlalchemy import text

            from services.agent_zero.database import get_db_schema, get_session_local

            schema = get_db_schema()
            _q = (lambda t: f'"{schema}"."{t}"'
                  if schema and schema != "public" else f'"{t}"')
            marks = ",".join(f":m{i}" for i in range(len(ids)))
            params = {f"m{i}": mid for i, mid in enumerate(ids)}
            SessionLocal = get_session_local()
            with SessionLocal() as db:
                rows = db.execute(
                    text(f"SELECT from_id, to_id FROM {_q('gdag_edges')} "
                         f"WHERE kind = 'about' AND from_id IN ({marks})"),
                    params,
                ).mappings().all()
            for r in rows:
                le = merged[r["from_id"]]["linked_entities"]
                if r["to_id"] not in le:
                    le.append(r["to_id"])
        except Exception as e:
            logger.debug("实体挂接查询失败: %s", e)

    out = sorted(merged.values(), key=lambda m: m["score"], reverse=True)
    out = out[:max(1, min(int(limit), 20))]
    _mark_recalled([m["id"] for m in out])
    return out


def _mark_recalled(ids: List[str]) -> None:
    """召回计数：整理时的价值分要用，但失败不影响召回本身。"""
    if not ids:
        return
    try:
        from services.agent_zero.database import get_session_local

        SessionLocal = get_session_local()
        now = time.time()
        with SessionLocal() as db:
            for mid in ids:
                db.execute(
                    text(f"UPDATE {_q('gdag_nodes')} SET props = json_set("
                         f"props, '$.hits', COALESCE(json_extract(props, '$.hits'), 0) + 1, "
                         f"'$.last_recalled_at', :ts) WHERE id = :id"),
                    {"ts": now, "id": mid},
                )
            db.commit()
    except Exception as e:
        logger.debug("记忆召回计数失败（不影响召回）: %s", e)


def _anchored_memories(query: str, workspace_id: Optional[str]) -> List[Dict[str, Any]]:
    """实体锚定二跳：query → 实体（文件/符号/概念）→ about 边 → 记忆。"""
    tokens = retrieve.tokenize(query)
    if not tokens or not workspace_id:
        return []
    from sqlalchemy import text

    from services.agent_zero.database import get_db_schema, get_session_local
    from services.agent_zero.services import codemap
    from services.agent_zero.stores.zero_store import decode_row

    schema = get_db_schema()
    def _q(table: str) -> str:
        return f'"{schema}"."{table}"' if schema and schema != "public" else f'"{table}"'

    # 实体候选：代码地图文件 + 显式创建的通用实体（按 query 命中）
    ent_ids: List[str] = []
    for e in codemap.get_map(workspace_id):
        hay = e["path"].lower() + " " + " ".join(s.lower() for s in e["symbols"])
        if any(t in hay for t in tokens):
            ent_ids.append(codemap.entity_id(workspace_id, e["path"]))
    try:
        params = {f"t{i}": f"%{t}%" for i, t in enumerate(tokens)}
        SessionLocal = get_session_local()
        like = " OR ".join(f"LOWER(CAST(props AS TEXT)) LIKE :t{i}"
                           for i in range(len(tokens)))
        with SessionLocal() as db:
            rows = db.execute(
                text(f"SELECT id FROM {_q('gdag_nodes')} "
                     f"WHERE camp = :camp AND type = 'entity' AND ({like})"),
                {"camp": workspace_id, **params},
            ).mappings().all()
        ent_ids.extend(r["id"] for r in rows)
    except Exception as e:
        logger.debug("实体候选查询失败: %s", e)
    ent_ids = list(dict.fromkeys(ent_ids))[:30]
    if not ent_ids:
        return []

    marks = ",".join(f":e{i}" for i in range(len(ent_ids)))
    params = {f"e{i}": eid for i, eid in enumerate(ent_ids)}
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT n.id, n.type, n.props, n.confidence, n.created_at, e.to_id "
                 f"FROM {_q('gdag_edges')} e "
                 f"JOIN {_q('gdag_nodes')} n ON n.id = e.from_id "
                 f"WHERE e.kind = 'about' AND e.to_id IN ({marks})"),
            params,
        ).mappings().all()

    by_id: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        d = decode_row(r)
        props = d.get("props") or {}
        if props.get("kind") != "memory":
            continue
        if props.get("archived"):
            continue   # 已归档不参与本体锚定召回
        mid = d["id"]
        kw = retrieve._keyword_score(tokens, props)
        created = d.get("created_at")
        fresh = retrieve._freshness(
            created.timestamp() if hasattr(created, "timestamp") else time.time())
        conf = float(d.get("confidence") or 0.0)
        # 锚定命中本身就是强信号（实体被提到 → 挂接记忆高度相关）
        score = 0.45 + 0.25 * kw + 0.15 * fresh + 0.10 * conf
        m = by_id.get(mid)
        if m is None:
            by_id[mid] = {
                "id": mid,
                "type": d["type"],
                "memory_type": props.get("memory_type") or "fact",
                "content": str(props.get("content") or ""),
                "tags": props.get("tags") or [],
                "workspace_id": props.get("workspace_id"),
                "source": props.get("source") or "manual",
                "score": round(score, 4),
                "linked_entities": [r["to_id"]],
            }
        elif r["to_id"] not in m["linked_entities"]:
            m["linked_entities"].append(r["to_id"])
    return list(by_id.values())


def forget(node_id: str) -> bool:
    """删除一条记忆（含关联边）。"""
    return _store.delete_node(str(node_id or "").strip())


# ── 管理面板 API（列表 / 统计 / 图谱 / 编辑 / 导出）────────────────


def _q(table: str) -> str:
    schema = get_db_schema()
    return f'"{schema}"."{table}"' if schema and schema != "public" else f'"{table}"'


def _where(camp: str, kind: Optional[str], source: Optional[str],
           tokens: List[str], include_archived: bool = False) -> tuple:
    conds = ["camp = :camp", "json_extract(props, '$.kind') = 'memory'"]
    params: Dict[str, Any] = {"camp": camp}
    if not include_archived:
        conds.append("COALESCE(json_extract(props, '$.archived'), 0) = 0")
    if kind:
        conds.append("json_extract(props, '$.memory_type') = :kind")
        params["kind"] = kind
    if source:
        conds.append("json_extract(props, '$.source') = :source")
        params["source"] = source
    if tokens:
        params.update({f"t{i}": f"%{t}%" for i, t in enumerate(tokens)})
        conds.append("(" + " OR ".join(
            f"LOWER(CAST(props AS TEXT)) LIKE :t{i}"
            for i in range(len(tokens))) + ")")
    return " AND ".join(conds), params


def _memory_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    props = d.get("props") or {}
    created = d.get("created_at")
    return {
        "id": d["id"],
        "memory_type": props.get("memory_type") or "fact",
        "content": str(props.get("content") or ""),
        "tags": props.get("tags") or [],
        "source": props.get("source") or "manual",
        "confidence": float(d.get("confidence") or 0.0),
        "created_at": str(created),
        "conversation_id": props.get("conversation_id"),
        "archived": bool(props.get("archived")),
        "archived_reason": props.get("archived_reason"),
        "superseded_by": props.get("superseded_by"),
        "hits": int(props.get("hits") or 0),
        "merged_count": int(props.get("merged_count") or 0),
        "linked_entities": [],
    }


def _attach_entities(db, items: List[Dict[str, Any]]) -> None:
    """给记忆条目补上挂接实体（一次查询）。"""
    ids = [m["id"] for m in items if m.get("id")]
    if not ids:
        return
    marks = ",".join(f":m{i}" for i in range(len(ids)))
    params = {f"m{i}": mid for i, mid in enumerate(ids)}
    rows = db.execute(
        text(f"SELECT from_id, to_id FROM {_q('gdag_edges')} "
             f"WHERE kind = 'about' AND from_id IN ({marks})"),
        params,
    ).mappings().all()
    by_id = {m["id"]: m for m in items}
    for r in rows:
        m = by_id.get(r["from_id"])
        if m is not None and r["to_id"] not in m["linked_entities"]:
            m["linked_entities"].append(r["to_id"])


def list_memories(workspace_id: Optional[str], *, kind: Optional[str] = None,
                  source: Optional[str] = None, q: Optional[str] = None,
                  sort: str = "recent", limit: int = 20,
                  offset: int = 0,
                  include_archived: bool = False) -> Dict[str, Any]:
    """分页列出工作区记忆（可按类型 / 来源 / 关键词过滤）。

    默认只列活跃记忆；include_archived=True 时连同「合并/淘汰归档」的旧条目一起列。
    """
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import decode_row

    camp = camp_for(workspace_id)
    tokens = retrieve.tokenize(q or "")
    where, params = _where(camp, kind, source, tokens, include_archived)
    order = ("created_at ASC" if sort == "oldest"
             else "confidence DESC, created_at DESC" if sort == "confidence"
             else "created_at DESC")
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        total = db.execute(
            text(f"SELECT COUNT(*) FROM {_q('gdag_nodes')} WHERE {where}"),
            params,
        ).scalar() or 0
        rows = db.execute(
            text(f"SELECT id, type, props, confidence, created_at "
                 f"FROM {_q('gdag_nodes')} WHERE {where} "
                 f"ORDER BY {order} LIMIT :limit OFFSET :offset"),
            {**params, "limit": max(1, min(int(limit), 100)),
             "offset": max(0, int(offset))},
        ).mappings().all()
        items = [_memory_dict(decode_row(r)) for r in rows]
        _attach_entities(db, items)
    return {"items": items, "total": int(total)}


def memory_stats(workspace_id: Optional[str]) -> Dict[str, Any]:
    """记忆统计：类型分布 / 来源 / 时间线 / 挂接实体 / 标签 / 置信度。"""
    from collections import Counter
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import decode_row

    camp = camp_for(workspace_id)
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id, type, props, confidence, created_at "
                 f"FROM {_q('gdag_nodes')} WHERE camp = :camp "
                 f"AND json_extract(props, '$.kind') = 'memory' "
                 f"AND COALESCE(json_extract(props, '$.archived'), 0) = 0 "
                 f"ORDER BY created_at DESC LIMIT 1000"),
            {"camp": camp},
        ).mappings().all()
        memories = [_memory_dict(decode_row(r)) for r in rows]
        archived_count = db.execute(
            text(f"SELECT COUNT(*) FROM {_q('gdag_nodes')} WHERE camp = :camp "
                 f"AND json_extract(props, '$.kind') = 'memory' "
                 f"AND COALESCE(json_extract(props, '$.archived'), 0) = 1"),
            {"camp": camp},
        ).scalar() or 0

        ent_rows = db.execute(
            text(f"SELECT e.to_id, COUNT(*) AS n, n.props "
                 f"FROM {_q('gdag_edges')} e "
                 f"JOIN {_q('gdag_nodes')} n ON n.id = e.to_id "
                 f"JOIN {_q('gdag_nodes')} m ON m.id = e.from_id "
                 f"WHERE e.kind = 'about' AND n.camp = :camp "
                 f"AND COALESCE(json_extract(m.props, '$.archived'), 0) = 0 "
                 f"GROUP BY e.to_id, n.props ORDER BY n DESC LIMIT 20"),
            {"camp": camp},
        ).mappings().all()
        entity_total = db.execute(
            text(f"SELECT COUNT(*) FROM {_q('gdag_nodes')} "
                 f"WHERE camp = :camp AND type = 'entity'"),
            {"camp": camp},
        ).scalar() or 0
        linked_ids = set(db.execute(
            text(f"SELECT DISTINCT e.from_id FROM {_q('gdag_edges')} e "
                 f"JOIN {_q('gdag_nodes')} n ON n.id = e.to_id "
                 f"JOIN {_q('gdag_nodes')} m ON m.id = e.from_id "
                 f"WHERE e.kind = 'about' AND n.camp = :camp "
                 f"AND COALESCE(json_extract(m.props, '$.archived'), 0) = 0"),
            {"camp": camp},
        ).scalars().all())

    kind_counts: Counter = Counter()
    source_counts: Counter = Counter()
    tag_counts: Counter = Counter()
    timeline: Counter = Counter()
    conf_sum = 0.0
    now = time.time()
    oldest: Optional[str] = None
    for m in memories:
        kind_counts[m["memory_type"]] += 1
        source_counts[m["source"]] += 1
        for t in m["tags"]:
            tag_counts[str(t)] += 1
        m["linked_entities"] = []  # 统计接口不需要逐条实体（图谱接口提供）
        if m["id"] not in linked_ids:
            m["_orphan"] = True
        conf_sum += m["confidence"]
        created = m.get("created_at") or ""
        if created and (oldest is None or created < oldest):
            oldest = created
        ts = _parse_ts(created)
        if ts and now - ts < 30 * 86400:
            timeline[time.strftime("%Y-%m-%d", time.localtime(ts))] += 1

    entities = []
    for r in ent_rows:
        props = decode_row(dict(r)).get("props") or {}
        label = props.get("name") or props.get("path") or r["to_id"]
        entities.append({"id": r["to_id"], "label": str(label), "count": int(r["n"])})

    return {
        "total": len(memories),
        "archived_count": int(archived_count),
        "kind_counts": {k: kind_counts.get(k, 0) for k in MEMORY_KINDS},
        "source_counts": dict(source_counts),
        "avg_confidence": round(conf_sum / len(memories), 3) if memories else 0,
        "oldest": oldest,
        "entity_count": int(entity_total),
        "linked_memory_count": len(linked_ids & {m["id"] for m in memories}),
        "orphan_memory_count": sum(1 for m in memories if m.get("_orphan")),
        "top_tags": [{"tag": t, "count": c} for t, c in tag_counts.most_common(12)],
        "top_entities": entities,
        "timeline": sorted(
            ({"date": d, "count": c} for d, c in timeline.items()),
            key=lambda x: x["date"],
        ),
    }


def _parse_ts(value: Any) -> Optional[float]:
    if hasattr(value, "timestamp"):
        return value.timestamp()
    try:
        return time.mktime(time.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return None


def memory_graph(workspace_id: Optional[str], limit: int = 150) -> Dict[str, Any]:
    """记忆 × 本体图谱：记忆节点 + 挂接实体 + references 依赖边。"""
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import decode_row

    camp = camp_for(workspace_id)
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        mem_rows = db.execute(
            text(f"SELECT id, type, props, confidence, created_at "
                 f"FROM {_q('gdag_nodes')} WHERE camp = :camp "
                 f"AND json_extract(props, '$.kind') = 'memory' "
                 f"AND COALESCE(json_extract(props, '$.archived'), 0) = 0 "
                 f"ORDER BY created_at DESC LIMIT :limit"),
            {"camp": camp, "limit": max(1, min(int(limit), 300))},
        ).mappings().all()
        memories = [_memory_dict(decode_row(r)) for r in mem_rows]
        _attach_entities(db, memories)

        mem_ids = [m["id"] for m in memories]
        marks = ",".join(f":m{i}" for i in range(len(mem_ids)))
        edge_rows = db.execute(
            text(f"SELECT from_id, to_id FROM {_q('gdag_edges')} "
                 f"WHERE kind = 'about' AND from_id IN ({marks})"),
            {f"m{i}": mid for i, mid in enumerate(mem_ids)},
        ).mappings().all()
        ent_ids = list(dict.fromkeys(r["to_id"] for r in edge_rows))
        ent_nodes: Dict[str, Dict[str, Any]] = {}
        if ent_ids:
            emarks = ",".join(f":e{i}" for i in range(len(ent_ids)))
            erows = db.execute(
                text(f"SELECT id, props FROM {_q('gdag_nodes')} "
                     f"WHERE id IN ({emarks})"),
                {f"e{i}": eid for i, eid in enumerate(ent_ids)},
            ).mappings().all()
            for r in erows:
                props = decode_row(r).get("props") or {}
                ent_nodes[r["id"]] = {
                    "kind": "entity",
                    "label": str(props.get("name") or props.get("path") or r["id"]),
                    "symbols": (props.get("symbols") or [])[:12],
                }
        ref_edges = []
        if len(ent_ids) > 1:
            rmarks = ",".join(f":e{i}" for i in range(len(ent_ids)))
            rrows = db.execute(
                text(f"SELECT from_id, to_id FROM {_q('gdag_edges')} "
                     f"WHERE kind = 'references' "
                     f"AND from_id IN ({rmarks}) AND to_id IN ({rmarks})"),
                {f"e{i}": eid for i, eid in enumerate(ent_ids)},
            ).mappings().all()
            ref_edges = [{"from_id": r["from_id"], "to_id": r["to_id"],
                         "kind": "references"} for r in rrows]

    nodes: List[Dict[str, Any]] = []
    for m in memories:
        nodes.append({
            "id": m["id"], "kind": "memory", "memory_type": m["memory_type"],
            "label": m["content"][:80], "snippet": m["content"][:220],
            "tags": m["tags"], "source": m["source"],
            "confidence": m["confidence"], "created_at": m["created_at"],
        })
    for eid, ent in ent_nodes.items():
        ent["id"] = eid
        nodes.append(ent)
    edges = [{"from_id": r["from_id"], "to_id": r["to_id"], "kind": "about"}
             for r in edge_rows] + ref_edges
    return {
        "nodes": nodes, "edges": edges,
        "memory_count": len(memories), "entity_count": len(ent_nodes),
        "truncated": len(memories) >= int(limit),
    }


def clear_memories(workspace_id: Optional[str]) -> Dict[str, Any]:
    """清空工作区的全部记忆（含实体挂接边）。"""
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local

    camp = camp_for(workspace_id)
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        ids = db.execute(
            text(f"SELECT id FROM {_q('gdag_nodes')} WHERE camp = :camp "
                 f"AND json_extract(props, '$.kind') = 'memory'"),
            {"camp": camp},
        ).scalars().all()
        if ids:
            marks = ",".join(f":m{i}" for i in range(len(ids)))
            params = {f"m{i}": mid for i, mid in enumerate(ids)}
            # 外键顺序：先删边再删节点
            db.execute(
                text(f"DELETE FROM {_q('gdag_edges')} "
                     f"WHERE from_id IN ({marks}) OR to_id IN ({marks})"),
                params,
            )
            db.execute(
                text(f"DELETE FROM {_q('gdag_nodes')} WHERE id IN ({marks})"),
                params,
            )
        db.commit()
    return {"removed": len(ids)}


# ── 整理记忆：合并重复 + 归档低价值（后台 / 手动）────────────────
# 设计取舍：
# - 整理只「归档」不硬删：用户仍能在抽屉里打开「含归档」看到并找回；
#   真正的删除只有两条路——对话里的 memory_forget、抽屉「清空」。
# - auto/llm 记忆：重复、长期未召回、低置信度、超上限都会归档。
# - manual 记忆（模型按用户要求写下的）：不会因「陈旧 / 低置信度」归档，
#   只有重复合并；当 camp 超过上限且 auto/llm 已被清干净，才按最旧的兜底归档，
#   保证上限真的生效（否则纯手写记忆的工作区会无限膨胀）。
# - 合并保留一条 keeper，其余标 archived + superseded_by，实体/标签/置信度
#   并入 keeper，召回仍然能通过 keeper 命中。

_consolidate_lock = threading.Lock()
_last_consolidate: Dict[str, float] = {}


def _active_memory_rows(camp: str) -> List[Dict[str, Any]]:
    """取一个 camp 的活跃记忆（整理用，带规范化正文缓存）。"""
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import decode_row

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id, type, camp, props, confidence, sources, created_at "
                 f"FROM {_q('gdag_nodes')} WHERE camp = :camp "
                 f"AND json_extract(props, '$.kind') = 'memory' "
                 f"AND COALESCE(json_extract(props, '$.archived'), 0) = 0 "
                 f"ORDER BY created_at DESC LIMIT 400"),
            {"camp": camp},
        ).mappings().all()
    out = []
    for r in rows:
        d = decode_row(r)
        props = d.get("props") or {}
        content = str(props.get("content") or "")
        out.append({
            **d,
            "camp": d.get("camp") or camp,
            "memory_type": props.get("memory_type") or "fact",
            "content": content,
            "tags": props.get("tags") or [],
            "hits": int(props.get("hits") or 0),
            "_norm": _norm_text(content),
        })
    return out


def _archive_memory(it: Dict[str, Any], reason: str,
                    superseded_by: Optional[str] = None) -> None:
    props = {
        **(it.get("props") or {}),
        "archived": True,
        "archived_at": time.time(),
        "archived_reason": reason,
    }
    if superseded_by:
        props["superseded_by"] = superseded_by
    try:
        _store.upsert_node({
            "id": it["id"], "type": it["type"],
            "camp": it.get("camp") or "",
            "props": props,
            "confidence": it.get("confidence"),
            "sources": it.get("sources"),
        })
    except Exception as e:
        logger.debug("记忆归档失败 %s: %s", it.get("id"), e)


def _merge_pair(keeper: Dict[str, Any], dup: Dict[str, Any]) -> None:
    """把 dup 并入 keeper：内容取更长的一版，标签/实体/置信度取并集。"""
    k_props = dict(keeper.get("props") or {})
    d_props = dict(dup.get("props") or {})
    content = keeper["content"] if len(keeper["_norm"]) >= len(dup["_norm"]) else dup["content"]
    tags = list(dict.fromkeys(
        [str(t) for t in (k_props.get("tags") or [])]
        + [str(t) for t in (d_props.get("tags") or [])]))[:8]
    source = "manual" if "manual" in (str(k_props.get("source") or ""),
                                       str(d_props.get("source") or "")) else (
        str(k_props.get("source") or d_props.get("source") or "auto"))
    hits = max(0, int(k_props.get("hits") or 0)) + max(0, int(d_props.get("hits") or 0))
    props = {
        **k_props,
        "title": content[:80],
        "content": content,
        "tags": tags,
        "source": source,
        "fingerprint": _fingerprint(content),
        "hits": hits,
        "merged_count": int(k_props.get("merged_count") or 0)
                        + int(d_props.get("merged_count") or 0) + 1,
        "last_merged_at": time.time(),
    }
    try:
        _store.upsert_node({
            "id": keeper["id"], "type": keeper["type"],
            "camp": keeper.get("camp") or "",
            "props": props,
            "confidence": max(float(keeper.get("confidence") or 0.0),
                              float(dup.get("confidence") or 0.0)),
            "sources": keeper.get("sources") or dup.get("sources"),
        })
    except Exception as e:
        logger.debug("记忆合并失败 %s <- %s: %s", keeper.get("id"), dup.get("id"), e)
        return
    # 同步内存态：同一 keeper 可能连续吞并多条，后续合并必须基于已更新的 props
    keeper["props"] = props
    keeper["confidence"] = max(float(keeper.get("confidence") or 0.0),
                               float(dup.get("confidence") or 0.0))
    keeper["hits"] = hits
    # 实体的 about 边转到 keeper（目标实体不变，只换起点），召回仍可命中
    try:
        from services.agent_zero.database import get_session_local

        SessionLocal = get_session_local()
        with SessionLocal() as db:
            db.execute(
                text(f"UPDATE {_q('gdag_edges')} SET from_id = :k "
                     f"WHERE from_id = :d AND kind = 'about'"),
                {"k": keeper["id"], "d": dup["id"]},
            )
            db.commit()
    except Exception as e:
        logger.debug("合并转移实体边失败: %s", e)
    keeper["content"] = content
    keeper["_norm"] = _norm_text(content)
    _archive_memory(dup, "duplicate", superseded_by=keeper["id"])


def _memory_value(it: Dict[str, Any], now: float) -> float:
    """整理时的价值分：置信度 × 新鲜度 × 召回次数（越高越值得留）。"""
    ts = _parse_ts(it.get("created_at")) or now
    age_days = max(0.0, (now - ts) / 86400.0)
    fresh = 1.0 / (1.0 + age_days / 30.0)
    return (float(it.get("confidence") or 0.0) * 0.6
            + fresh * 0.3
            + min(5, int(it.get("hits") or 0)) / 5.0 * 0.1)


def _active_memory_count(camp: str) -> int:
    from services.agent_zero.database import get_session_local

    SessionLocal = get_session_local()
    with SessionLocal() as db:
        return int(db.execute(
            text(f"SELECT COUNT(*) FROM {_q('gdag_nodes')} WHERE camp = :camp "
                 f"AND json_extract(props, '$.kind') = 'memory' "
                 f"AND COALESCE(json_extract(props, '$.archived'), 0) = 0"),
            {"camp": camp},
        ).scalar() or 0)


def _oldest_removable(camp: str, n: int,
                      sources: tuple = ("auto", "llm")) -> List[Dict[str, Any]]:
    """超量淘汰候选：按来源筛选，取最旧的 n 条活跃记忆。

    调用顺序决定优先级：先 auto/llm，若仍超上限才用 ("manual",) 兜底。
    """
    if n <= 0:
        return []
    from services.agent_zero.database import get_session_local
    from services.agent_zero.stores.zero_store import decode_row

    src_params = {f"s{i}": str(s) for i, s in enumerate(sources)}
    src_sql = ", ".join(f":s{i}" for i in range(len(sources)))
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        rows = db.execute(
            text(f"SELECT id, type, camp, props, confidence, sources, created_at "
                 f"FROM {_q('gdag_nodes')} WHERE camp = :camp "
                 f"AND json_extract(props, '$.kind') = 'memory' "
                 f"AND COALESCE(json_extract(props, '$.archived'), 0) = 0 "
                 f"AND json_extract(props, '$.source') IN ({src_sql}) "
                 f"ORDER BY created_at ASC LIMIT :n"),
            {"camp": camp, "n": int(n), **src_params},
        ).mappings().all()
    out = []
    for r in rows:
        d = decode_row(r)
        props = d.get("props") or {}
        out.append({
            **d,
            "camp": d.get("camp") or camp,
            "memory_type": props.get("memory_type") or "fact",
            "content": str(props.get("content") or ""),
            "tags": props.get("tags") or [],
            "hits": int(props.get("hits") or 0),
            "_norm": _norm_text(str(props.get("content") or "")),
        })
    return out


def consolidate(workspace_id: Optional[str]) -> Dict[str, Any]:
    """整理一个 camp 的记忆：合并近重复 → 归档低价值 → 超量淘汰。

    返回 {merged, archived, scanned}；重复执行无副作用（已归档不再参与）。
    """
    camp = camp_for(workspace_id)
    items = _active_memory_rows(camp)
    scanned = len(items)
    merged = 0
    archived = 0
    now = time.time()

    # 1) 合并近重复：同 kind 内按「价值 + 内容长度」排序，最完整的一条做 keeper
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        groups.setdefault(it["memory_type"], []).append(it)
    for group in groups.values():
        keepers: List[Dict[str, Any]] = []
        for it in sorted(group,
                         key=lambda x: (_memory_value(x, now), len(x["_norm"])),
                         reverse=True):
            target = None
            for k in keepers:
                if abs(len(k["_norm"]) - len(it["_norm"])) > max(len(it["_norm"]), 200):
                    continue
                thr = _dup_threshold(it["memory_type"],
                                     (it.get("props") or {}).get("source"),
                                     (k.get("props") or {}).get("source"))
                if _is_duplicate(it["_norm"], k["_norm"], thr):
                    target = k
                    break
            if target is None:
                keepers.append(it)
            else:
                _merge_pair(target, it)
                merged += 1
                archived += 1
                it["_archived"] = True

    # 2) 归档低价值：自动/蒸馏记忆、长期未召回 / 低置信度
    for it in items:
        if it.get("_archived"):
            continue
        source = str((it.get("props") or {}).get("source") or "")
        if source not in ("auto", "llm"):
            continue
        ts = _parse_ts(it.get("created_at")) or now
        age_days = (now - ts) / 86400.0
        hits = int(it.get("hits") or 0)
        if hits == 0 and age_days > ARCHIVE_AFTER_DAYS:
            _archive_memory(it, "stale")
            it["_archived"] = True
            archived += 1
        elif float(it.get("confidence") or 0.0) < LOW_CONFIDENCE and age_days > LOW_CONF_DAYS:
            _archive_memory(it, "low_value")
            it["_archived"] = True
            archived += 1

    # 3) 超量淘汰：按总数算溢出，优先归档最旧的 auto/llm；若仍超上限，
    #    才按最旧的 manual 兜底（手写记忆不会因陈旧/低价值归档，但上限必须生效）。
    #    （合并扫描有条数上限，不能只用扫描结果算溢出，否则大 camp 永远清不到上限）
    overflow = _active_memory_count(camp) - CAMP_MAX_MEMORIES
    if overflow > 0:
        picked = _oldest_removable(camp, overflow, ("auto", "llm"))
        if len(picked) < overflow:
            picked += _oldest_removable(camp, overflow - len(picked), ("manual",))
        for it in picked:
            _archive_memory(it, "overflow")
            archived += 1

    if merged or archived:
        logger.info("记忆整理 camp=%s 扫描=%d 合并=%d 归档=%d",
                    camp, scanned, merged, archived)
    return {"merged": merged, "archived": archived, "scanned": scanned}


def schedule_consolidate(workspace_id: Optional[str]) -> None:
    """run 结束后在后台整理（同一 camp 10 分钟内只跑一次，不阻塞对话）。"""
    camp = camp_for(workspace_id)
    now = time.time()
    with _consolidate_lock:
        if now - float(_last_consolidate.get(camp) or 0.0) < CONSOLIDATE_MIN_INTERVAL:
            return
        _last_consolidate[camp] = now

    def _run() -> None:
        try:
            consolidate(workspace_id)
        except Exception as e:
            logger.debug("后台记忆整理失败 camp=%s: %s", camp, e)

    threading.Thread(target=_run, daemon=True).start()


# ── 重建记忆：后台线程 + LLM 蒸馏 ──────────────────────────────
# 重建不阻塞请求：POST 立即返回，后台线程逐批调用 LLM 蒸馏记忆；
# LLM 不可用/失败时退回确定性提取（与 run 结束自动沉淀同一口径）。
REBUILD_MAX_CONVERSATIONS = 40
REBUILD_MAX_MEMORIES = 80
REBUILD_BATCH = 8

_rebuild_jobs: Dict[str, Dict[str, Any]] = {}
_rebuild_lock = threading.Lock()

_REBUILD_SYSTEM = (
    "你是 AtlasX 工作区的长期记忆蒸馏引擎。根据提供的对话执行摘要，提炼值得长期保存的记忆。\n"
    "输出严格 JSON 数组（无解释、无代码块）：\n"
    '[{"kind": "fact|decision|preference|experience", "content": "自包含的记忆内容(≤300字)", '
    '"entities": ["相关文件或符号名"]}]\n'
    "规则：合并重复经验；只保留对后续工作有用的信息（决策、结论、坑、产物位置）；"
    "不得虚构摘要中没有的内容；每个对话最多 3 条记忆。"
)


def _collect_run_digests(ws_id: str) -> List[Dict[str, Any]]:
    """确定性采集对话的执行摘要（LLM 蒸馏与回退共用素材）。"""
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local

    digests: List[Dict[str, Any]] = []
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        conv_rows = db.execute(
            text(f"SELECT id, title FROM {_q('conversations')} "
                 f"WHERE workspace_id = :ws ORDER BY created_at DESC LIMIT :n"),
            {"ws": ws_id, "n": REBUILD_MAX_CONVERSATIONS},
        ).mappings().all()
        for conv in conv_rows:
            run_rows = db.execute(
                text(f"SELECT id, content FROM {_q('conversation_runs')} "
                     f"WHERE conversation_id = :cid "
                     f"AND status IN ('done', 'failed', 'stopped') "
                     f"ORDER BY created_at DESC LIMIT 10"),
                {"cid": conv["id"]},
            ).mappings().all()
            for run in run_rows:
                ev_rows = db.execute(
                    text(f"SELECT data FROM {_q('conversation_run_events')} "
                         f"WHERE run_id = :rid AND kind = 'tool_result' ORDER BY seq ASC"),
                    {"rid": run["id"]},
                ).scalars().all()
                tool_counts: Dict[str, int] = {}
                artifacts: List[str] = []
                failures: List[str] = []
                ends = 0
                for raw in ev_rows:
                    try:
                        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    except Exception:
                        continue
                    trace = data.get("tool_result") or data
                    ends += 1
                    name = str(trace.get("name") or "")
                    tool_counts[name] = tool_counts.get(name, 0) + 1
                    if not trace.get("ok", True):
                        failures.append(str(trace.get("summary") or name)[:80])
                    disp = trace.get("display")
                    if isinstance(disp, dict) and disp.get("type") == "diff" and disp.get("path"):
                        path = str(disp["path"])
                        if path not in artifacts:
                            artifacts.append(path)
                if ends < 2:
                    continue  # 纯问答/闲聊不值得沉淀
                tools_line = ", ".join(f"{k}×{v}" for k, v in sorted(tool_counts.items()))
                lines = [
                    f"目标：{str(conv.get('title') or '').strip()[:200]}",
                    f"工具：{tools_line[:300]}",
                ]
                if artifacts:
                    lines.append(f"产物：{', '.join(artifacts[:12])}")
                if failures:
                    lines.append(f"失败步骤：{'; '.join(failures[:5])}")
                final = str(run.get("content") or "").strip()
                if final:
                    lines.append(f"结论：{final[:300]}")
                digests.append({
                    "conv_id": conv["id"],
                    "title": str(conv.get("title") or "").strip(),
                    "digest": "\n".join(lines),
                    "artifacts": artifacts,
                })
    return digests


def _ingest_digest(ws_id: str, d: Dict[str, Any]) -> None:
    """确定性写入一条 experience（LLM 失败时的回退口径）。"""
    remember(d["digest"], kind="experience", workspace_id=ws_id,
             conversation_id=d["conv_id"], tags=["run", "auto", "rebuild"],
             entities=d["artifacts"], source="auto")


def _parse_json_array(raw: str) -> List[Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    try:
        data = json.loads(text)
    except Exception:
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end <= start:
            raise ValueError("LLM 输出中没有 JSON 数组")
        data = json.loads(text[start:end + 1])
    if isinstance(data, dict):
        data = data.get("memories") or []
    return data if isinstance(data, list) else []


def _llm_distill(ws_id: str, digests: List[Dict[str, Any]],
                 job: Dict[str, Any]) -> int:
    """逐批调用 LLM 蒸馏记忆；单批失败时该批确定性回退。返回写入条数。"""
    import asyncio

    from services.agent_zero.runtime.zero import llm as llm_mod

    code = llm_mod.get_active_model_code() or "default"
    created = 0
    for i in range(0, len(digests), REBUILD_BATCH):
        if created >= REBUILD_MAX_MEMORIES:
            break
        chunk = digests[i:i + REBUILD_BATCH]
        payload = "\n\n".join(
            f"[对话 {j + 1}] {d['title']}\n{d['digest']}"
            for j, d in enumerate(chunk)
        )
        items: Optional[List[Any]] = None
        try:
            res = asyncio.run(llm_mod.llm_chat(
                code,
                [{"role": "system", "content": _REBUILD_SYSTEM},
                 {"role": "user", "content": payload}],
                temperature=0.2, max_tokens=1200, timeout=120.0,
            ))
            job["llm_calls"] = job.get("llm_calls", 0) + 1
            items = _parse_json_array(res.content)
        except Exception as e:
            logger.warning("重建 LLM 蒸馏失败（确定性回退）: %s", e)
            job["errors"].append(str(e)[:200])
        if items is None:
            for d in chunk:
                _ingest_digest(ws_id, d)
                created += 1
                if created >= REBUILD_MAX_MEMORIES:
                    return created
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            content = str(it.get("content") or "").strip()
            if not content:
                continue
            kind = str(it.get("kind") or "experience")
            remember(content, kind=kind if kind in MEMORY_KINDS else "experience",
                     workspace_id=ws_id,
                     entities=[str(x) for x in (it.get("entities") or []) if str(x).strip()],
                     confidence=0.75, source="llm", tags=["rebuild", "llm"])
            created += 1
            if created >= REBUILD_MAX_MEMORIES:
                break
    return created


def _rebuild_worker(ws_id: str, job: Dict[str, Any]) -> None:
    try:
        job["removed"] = clear_memories(ws_id)["removed"]
        digests = _collect_run_digests(ws_id)
        job["conversations"] = len({d["conv_id"] for d in digests})
        job["created"] = _llm_distill(ws_id, digests, job) if digests else 0
        # 蒸馏产出再整理一次：合并近重复 + 归档低价值，避免重建后又是满屏重复
        job["consolidated"] = consolidate(ws_id)
        job["status"] = "done"
        job["finished_at"] = time.time()
    except Exception as e:
        logger.exception("记忆重建失败")
        job["status"] = "error"
        job["errors"].append(str(e)[:300])
        job["finished_at"] = time.time()


def start_rebuild(workspace_id: Optional[str]) -> Dict[str, Any]:
    """启动后台重建；若已有运行中的任务则返回其状态。"""
    camp = camp_for(workspace_id)
    with _rebuild_lock:
        job = _rebuild_jobs.get(camp)
        if job and job.get("status") == "running":
            return dict(job)
        job = {"status": "running", "started_at": time.time(), "removed": 0,
               "created": 0, "conversations": 0, "llm_calls": 0, "errors": []}
        _rebuild_jobs[camp] = job
    threading.Thread(target=_rebuild_worker, args=(workspace_id, job), daemon=True).start()
    return dict(job)


def rebuild_status(workspace_id: Optional[str]) -> Dict[str, Any]:
    job = _rebuild_jobs.get(camp_for(workspace_id))
    return dict(job) if job else {"status": "idle"}


def export_markdown(workspace_id: Optional[str]) -> str:
    """把工作区记忆导出为 Markdown（按类型分组，含挂接实体）。"""
    data = list_memories(workspace_id, limit=100)
    ws_name = ""
    try:
        ws = zero_store.get_workspace(camp_for(workspace_id))
        ws_name = str((ws or {}).get("name") or workspace_id or "")
    except Exception:
        pass
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for m in data["items"]:
        groups.setdefault(m["memory_type"], []).append(m)
    lines = [f"# 记忆导出 · {ws_name}", "",
             f"共 {data['total']} 条 · 导出时间 {time.strftime('%Y-%m-%d %H:%M')}", ""]
    for kind in MEMORY_KINDS:
        items = groups.get(kind) or []
        if not items:
            continue
        lines.append(f"## {kind}（{len(items)}）")
        for m in items:
            lines.append(f"- {m['content']}")
            meta = []
            if m["tags"]:
                meta.append("标签: " + ", ".join(m["tags"]))
            if m["linked_entities"]:
                meta.append("实体: " + ", ".join(
                    e.split(":")[-1] for e in m["linked_entities"]))
            if meta:
                lines.append(f"  - {' · '.join(meta)} · {m['created_at']}")
        lines.append("")
    return "\n".join(lines)


def capture_run_experience(conv: Dict[str, Any], user_message: str,
                           role: Dict[str, Any],
                           sink: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """run 结束自动沉淀一条 experience（纯结构化提取，不调 LLM）。

    条件：本轮至少调了 2 个工具——纯问答/闲聊不值得沉淀。
    """
    trace = sink.get("tool_trace") or []
    if len(trace) < 2:
        return None
    tool_counts: Dict[str, int] = {}
    artifacts: List[str] = []
    failures: List[str] = []
    for t in trace:
        name = str(t.get("name") or "")
        tool_counts[name] = tool_counts.get(name, 0) + 1
        if not t.get("ok", True):
            failures.append(str(t.get("summary") or name)[:80])
        disp = t.get("display")
        if isinstance(disp, dict) and disp.get("type") == "diff" and disp.get("path"):
            path = str(disp["path"])
            if path not in artifacts:
                artifacts.append(path)
    tools_line = ", ".join(f"{k}×{v}" for k, v in sorted(tool_counts.items()))
    lines = [
        f"目标：{str(user_message or '').strip()[:200]}",
        f"工具：{tools_line[:300]}",
    ]
    if artifacts:
        lines.append(f"产物：{', '.join(artifacts[:12])}")
    if failures:
        lines.append(f"失败步骤：{'; '.join(failures[:5])}")
    final = str(sink.get("text") or "").strip()
    if final:
        lines.append(f"结论：{final[:300]}")
    try:
        return remember("\n".join(lines), kind="experience",
                        workspace_id=conv.get("workspace_id"),
                        conversation_id=conv.get("id"),
                        tags=["run", "auto"],
                        entities=artifacts,
                        confidence=0.55,
                        source="auto")
    except Exception as e:
        logger.debug("自动记忆沉淀失败 conv=%s: %s", conv.get("id"), e)
        return None
