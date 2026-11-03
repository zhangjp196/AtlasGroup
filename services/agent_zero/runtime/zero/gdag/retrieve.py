"""GDAG 规划 / 经验检索（v1.1：纯关系，不用向量）。

检索 = camp 作用域硬过滤 + 关键词/结构评分：
- 关键词：确定性分词（ASCII 词 + CJK bigram，零依赖）→ props::text ILIKE 候选
- 结构：节点类型权重 + 度数 + 任务 DAG 指纹（节点类型直方图余弦重叠）
- 新鲜度 / 置信度：时间衰减 + confidence 加权

规模上量若确需语义检索，再评估引入向量库（与关系存储解耦），本模块接口不变。
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from services.agent_zero.database import get_db_schema, get_session_local
from services.agent_zero.runtime.zero.gdag.store import GdagStore

logger = logging.getLogger(__name__)

# 节点类型结构权重（规划/经验检索中越"可复用"越高）
_TYPE_WEIGHT = {
    "experience": 1.0,
    "task": 0.9,
    "step": 0.7,
    "fact": 0.6,
    "entity": 0.5,
    "relation": 0.5,
    "artifact": 0.4,
    "tool_call": 0.3,
}

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_RE = re.compile(r"[A-Za-z0-9_]{2,}")


def _schema() -> str:
    return get_db_schema()


def _q(table: str) -> str:
    s = _schema()
    return f'"{s}"."{table}"' if s and s != "public" else f'"{table}"'


def tokenize(query: str) -> List[str]:
    """确定性分词：ASCII 词（>=2）+ CJK 字符 bigram（孤字取单字）。"""
    low = (query or "").lower()
    out: List[str] = []
    out.extend(m.group(0) for m in _ASCII_RE.finditer(low))
    for m in _CJK_RE.finditer(low):
        run = m.group(0)
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i : i + 2] for i in range(len(run) - 1))
    return out


def _node_text(props: Dict[str, Any]) -> str:
    """节点可检索文本：标题 / 目标 / 摘要 / 正文等常见字段。

    `content` 必须包含：记忆正文存在 props.content，没有它关键词评分
    只看 title（前 80 字），长记忆的检索命中率会显著下降。
    """
    fields = ("title", "goal", "name", "summary", "text", "description",
              "acceptance", "content")
    parts = [str(props.get(f)) for f in fields if props.get(f)]
    return " ".join(parts)


def _keyword_score(query_tokens: List[str], props: Dict[str, Any]) -> float:
    """关键词命中率（0-1）：节点可检索文本命中的 query 词占比。"""
    if not query_tokens:
        return 0.0
    text = _node_text(props).lower()
    if not text:
        return 0.0
    hit = sum(1 for t in set(query_tokens) if t in text)
    return hit / len(set(query_tokens))


def _freshness(created_at: float, half_life_days: float = 90.0) -> float:
    """时间衰减（半衰期）。"""
    age_days = max(0.0, (time.time() - created_at) / 86400.0)
    return 0.5 ** (age_days / half_life_days)


def _fetch_candidates(store: GdagStore, camp: str, query: str,
                      limit: int = 200) -> List[Dict[str, Any]]:
    """候选集：camp 硬过滤；有词元时按 props::text ILIKE 命中，无命中回退近期节点。"""
    tokens = tokenize(query)
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        if tokens:
            params: Dict[str, Any] = {"camp": camp, "limit": limit}
            params.update({f"t{i}": f"%{t}%" for i, t in enumerate(tokens)})
            like = " OR ".join(f"LOWER(CAST(props AS TEXT)) LIKE :t{i}"
                               for i in range(len(tokens)))
            sql = (
                f"SELECT id, type, camp, props, confidence, sources, created_at "
                f"FROM {_q('gdag_nodes')} WHERE camp = :camp "
                f"AND ({like}) "
                f"ORDER BY created_at DESC LIMIT :limit"
            )
        else:
            sql = (
                f"SELECT id, type, camp, props, confidence, sources, created_at "
                f"FROM {_q('gdag_nodes')} WHERE camp = :camp "
                f"ORDER BY created_at DESC LIMIT :limit"
            )
            params = {"camp": camp, "limit": limit}
        rows = db.execute(text(sql), params).mappings().all()
    if not rows and tokens:
        # 关键词零命中 → 回退 camp 近期节点（纯结构/新鲜度评分）
        return _fetch_candidates(store, camp, "", limit=50)
    from services.agent_zero.stores.zero_store import decode_row

    out = []
    for r in rows:
        d = decode_row(r)
        out.append({
            "id": d["id"], "type": d["type"], "camp": d["camp"],
            "props": d["props"] or {}, "confidence": d["confidence"],
            "sources": d["sources"], "created_at": d["created_at"],
        })
    return out


def _degrees(store: GdagStore, node_ids: List[str]) -> Counter:
    """批量度数（入边 + 出边）。"""
    if not node_ids:
        return Counter()
    SessionLocal = get_session_local()
    with SessionLocal() as db:
        marks = ",".join(f":n{i}" for i in range(len(node_ids)))
        params = {f"n{i}": nid for i, nid in enumerate(node_ids)}
        rows = db.execute(
            text(f"SELECT from_id, to_id FROM {_q('gdag_edges')} "
                 f"WHERE from_id IN ({marks}) OR to_id IN ({marks})"),
            params,
        ).mappings().all()
    deg: Counter = Counter()
    for r in rows:
        deg[r["from_id"]] += 1
        deg[r["to_id"]] += 1
    return deg


def scored_search(store: GdagStore, camp: str, query: str,
                  top_k: int = 10, types: Optional[Tuple[str, ...]] = None
                  ) -> List[Dict[str, Any]]:
    """关键词 + 结构 + 新鲜度 + 置信度 加权评分，返回 top_k。

    每项：{node, score, reasons}。
    """
    candidates = _fetch_candidates(store, camp, query)
    if types:
        candidates = [c for c in candidates if c["type"] in types]
    if not candidates:
        return []
    tokens = tokenize(query)
    degs = _degrees(store, [c["id"] for c in candidates])

    results = []
    for c in candidates:
        kw = _keyword_score(tokens, c["props"])
        struct = min(1.0, _TYPE_WEIGHT.get(c["type"], 0.3) * 0.7 + min(degs.get(c["id"], 0), 5) / 5.0 * 0.3)
        fresh = _freshness(c["created_at"].timestamp() if hasattr(c["created_at"], "timestamp") else time.time())
        conf = float(c["confidence"] or 0.0)
        score = 0.5 * kw + 0.3 * struct + 0.15 * fresh + 0.05 * conf
        reasons = {
            "keyword": round(kw, 3), "structure": round(struct, 3),
            "freshness": round(fresh, 3), "confidence": conf,
        }
        results.append({"node": c, "score": round(score, 4), "reasons": reasons})
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[: max(1, min(int(top_k), 50))]


# ── 任务 DAG 结构相似度（快速路径：复用历史 DAG 骨架）─────────


