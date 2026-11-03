"""工具后台任务注册表：把「等待工具跑完」改成「监督工具状态」。

## 为什么需要

过去一轮对话里，模型发出工具调用后主循环会 `await` 到工具结束才继续：

    while not tool_task.done():
        yield {"ping": ...}          # 只有心跳，模型完全被挡住

于是出现两类问题（2026-10-29 现象）：

1. 一条 `bash build.sh | tail -25` 挂住（子进程退出但管道没关），整轮 run
   就静止十几分钟，模型无法做别的、也无法察觉「这个工具不对劲」；
2. 慢工具（联网 / 长构建 / 全仓库扫描）期间模型没有决策权，用户只能看着
   活动条等他，无法「先干别的，回头再收结果」。

## 现在的语义

- 前台仍正常等待，但只等 `soft_budget_s`（默认 45s）；
- 超过预算仍没结束 → **把任务交给本注册表托管**，立刻给模型一条
  `{"status": "background", "job_id": ...}` 结果，模型继续往下走；
- 模型用工具 `tool_job`（action=list/status/result/wait/kill）**主动监督**：
  查看有没有跑完、读输出、必要时杀掉；
- 任务完成时自己往 run 事件流补一条 `tool_result`，前端那条 tool 条目
  就地更新为 done，不需要模型再问一次。

注册表是**进程内**的：任务持有强引用（防 GC），按 job_id 查找，
退出/异常都会落到 job 对象上，绝不静默丢结果。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# job 结果里回给模型的原文上限（避免一条超长输出把上下文顶爆）
RESULT_MAX_CHARS = 8_000
# 保留的已结束 job 数量（按 conversation 计），防止常驻内存无限增长
KEEP_FINISHED = 40

_JOBS: Dict[str, Dict[str, Any]] = {}
# 强引用：asyncio 只保弱引用，任务可能被 GC 掉（历史踩过）
_TASKS: Dict[str, asyncio.Task] = {}


def _clip(text: str) -> str:
    s = str(text or "")
    if len(s) <= RESULT_MAX_CHARS:
        return s
    head = RESULT_MAX_CHARS // 2
    tail = RESULT_MAX_CHARS - head
    return s[:head] + f"\n…（中间省略 {len(s) - RESULT_MAX_CHARS} 字符）…\n" + s[-tail:]


def _prune(conv_id: str) -> None:
    done = [j for j in _JOBS.values()
            if j.get("conversation_id") == conv_id
            and j.get("status") != "running"]
    done.sort(key=lambda j: float(j.get("finished_at") or 0))
    for job in done[:max(0, len(done) - KEEP_FINISHED)]:
        _JOBS.pop(job["id"], None)
        _TASKS.pop(job["id"], None)


def _emit_result(run_id: str, job: Dict[str, Any]) -> None:
    """任务收尾时往 run 事件流补一条 tool_result（前端就地更新那条工具条目）。"""
    if not run_id:
        return
    try:
        from services.agent_zero.task_layer import run_service

        payload = {
            "id": job.get("call_id") or "",
            "name": job.get("name"),
            "args": job.get("args") or {},
            "duration_ms": int((float(job.get("finished_at") or 0)
                                - float(job.get("started_at") or 0)) * 1000),
            "ok": job.get("status") == "done",
            "summary": _clip(str(job.get("result") or job.get("error") or ""))[:600],
            "background": True,
            "job_id": job["id"],
        }
        run_service.append_event(run_id, "tool_result", {"tool_result": payload})
    except Exception:
        logger.debug("后台工具结果事件推送失败", exc_info=True)


def _finish(job: Dict[str, Any], *, status: str,
            result: Any = None, error: str = "") -> None:
    job["status"] = status
    job["finished_at"] = time.time()
    job["elapsed_s"] = round(job["finished_at"] - float(job.get("started_at") or 0), 1)
    if result is not None:
        text = result if isinstance(result, str) else json.dumps(
            result, ensure_ascii=False, default=str)
        job["result"] = text
    if error:
        job["error"] = error
    _emit_result(str(job.get("run_id") or ""), job)


async def _post_hook(job: Dict[str, Any], status: str, text: str) -> str:
    """后台工具完成时补跑 PostToolUse 钩子（与前台路径行为一致）。"""
    try:
        from services.agent_zero.services import hooks_service
        from services.agent_zero.task_layer import conversation

        conv = conversation.get_conversation(str(job.get("conversation_id") or ""))
        ws = str((conv or {}).get("workspace_id") or "")
        if not hooks_service.has_hooks(ws):
            return text
        result = await hooks_service.post_tool_use(
            ws, str(job.get("name") or ""), job.get("args") or {}, text,
            status == "done", run_id=str(job.get("run_id") or ""),
            session_id=str(job.get("conversation_id") or ""))
        notes: List[str] = []
        if result.get("context"):
            notes.append(str(result["context"]))
        if result.get("decision") == "deny":
            notes.append("钩子要求阻止继续：" + str(result.get("reason") or "（无说明）"))
        if notes:
            text = text + "\n\n[PostToolUse 钩子反馈]\n" + "\n\n".join(notes)
    except Exception:
        logger.debug("后台工具 PostToolUse 钩子执行失败（忽略）", exc_info=True)
    return text


async def _watch(job_id: str) -> None:
    job = _JOBS.get(job_id)
    task = _TASKS.get(job_id)
    if job is None or task is None:
        return
    try:
        value = await task
        text = value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False, default=str)
        text = await _post_hook(job, "done", text)
        _finish(job, status="done", result=text)
    except asyncio.CancelledError:
        _finish(job, status="killed", error="已停止（用户停止运行或主动 kill）")
    except Exception as exc:                      # noqa: BLE001
        logger.warning("后台工具 %s 执行失败: %s", job.get("name"), exc)
        msg = f"{type(exc).__name__}: {exc}"
        msg = await _post_hook(job, "failed", msg)
        _finish(job, status="failed", error=msg)


def adopt(*, conversation_id: str, run_id: str, name: str,
          args: Optional[Dict[str, Any]] = None,
          task: asyncio.Task, call_id: str = "",
          started_at: Optional[float] = None) -> Dict[str, Any]:
    """把一个**已在运行**的 asyncio 任务转为后台 job（前台超时转交时用）。"""
    job_id = "job-" + uuid.uuid4().hex[:12]
    job: Dict[str, Any] = {
        "id": job_id,
        "conversation_id": conversation_id or "",
        "run_id": run_id or "",
        "name": name,
        "args": args or {},
        "call_id": call_id,
        "status": "running",
        "started_at": float(started_at or time.time()),
        "finished_at": 0.0,
        "result": "",
        "error": "",
    }
    _JOBS[job_id] = job
    _TASKS[job_id] = task
    # 用任务自己的事件循环创建监视线程：调用方可能不在当前 loop 上下文里
    # （测试会新建 loop 后直接 adopt），ensure_future 会去取默认 loop 而失败。
    task.get_loop().create_task(_watch(job_id))
    _prune(conversation_id or "")
    logger.info("工具转入后台监督: %s job=%s conv=%s", name, job_id, conversation_id)
    return job


def start(*, conversation_id: str, run_id: str, name: str,
          coro: Any, args: Optional[Dict[str, Any]] = None,
          call_id: str = "") -> Dict[str, Any]:
    """直接以后台方式启动一个新工具（模型显式要求后台执行时用）。"""
    task = asyncio.ensure_future(coro)
    return adopt(conversation_id=conversation_id, run_id=run_id, name=name,
                 args=args, task=task, call_id=call_id)


def get(job_id: str) -> Optional[Dict[str, Any]]:
    return _JOBS.get(str(job_id or ""))


def list_jobs(conversation_id: str, running_only: bool = False) -> List[Dict[str, Any]]:
    jobs = [j for j in _JOBS.values()
            if j.get("conversation_id") == conversation_id
            and (not running_only or j.get("status") == "running")]
    jobs.sort(key=lambda j: float(j.get("started_at") or 0))
    return jobs


def kill(job_id: str, reason: str = "模型主动终止") -> bool:
    task = _TASKS.get(str(job_id or ""))
    job = _JOBS.get(str(job_id or ""))
    if task is None or job is None:
        return False
    if job.get("status") != "running":
        return False
    task.cancel()
    job["status"] = "killed"
    job["error"] = reason
    job["finished_at"] = time.time()
    job["elapsed_s"] = round(job["finished_at"] - float(job.get("started_at") or 0), 1)
    return True


def kill_conversation(conversation_id: str, reason: str = "运行已停止") -> int:
    """对话停止时把它名下的后台工具一并杀掉（否则「停不下来」）。"""
    n = 0
    for job in list_jobs(conversation_id, running_only=True):
        if kill(job["id"], reason):
            n += 1
    return n


def summary(job: Dict[str, Any], *, with_result: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "job_id": job["id"],
        "name": job.get("name"),
        "status": job.get("status"),
        "elapsed_s": round(time.time() - float(job.get("started_at") or 0), 1),
        "args": job.get("args") or {},
    }
    err = str(job.get("error") or "")
    if err:
        out["error"] = err
    if with_result and job.get("status") != "running":
        out["result"] = _clip(str(job.get("result") or ""))
    elif job.get("status") == "running":
        out["note"] = "仍在运行；可继续别的工作，稍后再查，或用 action=kill 终止"
    return out


async def handle(conversation_id: str, args: Dict[str, Any]) -> Any:
    """工具 `tool_job` 的实现：list / status / result / wait / kill。"""
    action = str((args or {}).get("action") or "list").strip().lower()
    job_id = str((args or {}).get("job_id") or "").strip()
    if action == "list":
        jobs = list_jobs(conversation_id)
        return {
            "jobs": [summary(j) for j in jobs],
            "running": sum(1 for j in jobs if j.get("status") == "running"),
            "note": ("没有后台工具在跑" if not jobs
                     else "用 action=result（带 job_id）读取输出"),
        }
    job = get(job_id)
    if job is None:
        return {"error": f"找不到后台任务 {job_id}（可能已回收，用 action=list 查看）"}
    if job.get("conversation_id") != conversation_id:
        return {"error": "该后台任务不属于当前对话"}
    if action == "status":
        return summary(job)
    if action == "result":
        return summary(job, with_result=True)
    if action == "kill":
        ok = kill(job_id)
        return {"ok": ok, **summary(get(job_id) or job)}
    if action == "wait":
        timeout = float((args or {}).get("timeout_s") or 30)
        timeout = max(1.0, min(timeout, 300.0))
        task = _TASKS.get(job_id)
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        return summary(get(job_id) or job, with_result=True)
    return {"error": f"未知 action: {action}（可用 list / status / result / wait / kill）"}


def running_count(conversation_id: str) -> int:
    return len(list_jobs(conversation_id, running_only=True))
