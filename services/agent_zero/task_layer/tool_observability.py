"""Model-facing tool results and compact user-visible activity summaries."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from services.agent_zero.runtime.zero import file_ops, file_tools


def _args_skeleton(args: Dict[str, Any]) -> Dict[str, Any]:
    """参数过大时只留结构（键 + 类型/长度），**不外发值本身**。

    大字段往往是文件正文 / 长文本，回显前 40 字符既能塞爆 SSE 又可能泄内容；
    改成「类型 + 字符数」既够定位问题，又不碰敏感数据。
    """
    out: Dict[str, Any] = {}
    for k, v in list((args or {}).items())[:12]:
        if isinstance(v, (dict, list)):
            out[str(k)] = f"<{type(v).__name__} len={len(v)}>"
        elif isinstance(v, str):
            out[str(k)] = f"<str len={len(v)}>"
        else:
            out[str(k)] = v
    return out


def _args_previewable(args: Dict[str, Any]) -> bool:
    """参数小且全是短字符串才原样外发。"""
    try:
        blob = json.dumps(args or {}, ensure_ascii=False)
    except Exception:
        return False
    if len(blob) > 800:
        return False
    def _leaf_ok(v: Any) -> bool:
        if isinstance(v, str):
            return len(v) <= 200
        if isinstance(v, (int, float, bool)) or v is None:
            return True
        return _leaf_ok(json.dumps(v, ensure_ascii=False))
    try:
        return all(_leaf_ok(v) for v in (args or {}).values())
    except Exception:
        return False


def _result_is_error(result: str) -> bool:
    """工具结果是否表示失败（约定：JSON 里的 error 字段）。"""
    try:
        obj = json.loads(result)
        return bool(isinstance(obj, dict) and obj.get("error"))
    except Exception:
        return False



def _model_facing_text(result: str) -> str:
    """给模型的工具结果正文：有 output 信封就剥掉壳（与任务链路同一函数）。"""
    unwrapped = file_ops.unwrap_display(result)
    return unwrapped if isinstance(unwrapped, str) else (result or "")


def _result_display(result: str) -> Optional[Dict[str, Any]]:
    """给前端的展示载荷（无则 None，不塞进 trace 免得白占地方）。"""
    return file_tools.envelope_of(result)


def _result_summary(result: str) -> str:
    """结果摘要：给一句人话 + 大小，不外发全文。

    文件类工具带 display 载荷时按载荷说（路径 / 行号区间 / diff 条数），
    否则会退化成「返回字段：output, display」——那时明明有路径可写，对用户零信息量。
    """
    return file_tools.brief(result, size=len(result or ""))
