"""对话运行 worker：把一轮对话回复从 HTTP 连接上解耦，服务端后台执行。

进程内后台循环：从 `conversation_runs`
取 queued → `claim_run`（CAS）→ 跑 `conversation.generate_reply_stream` →
逐事件落库（`run_service.append_event`）→ 收尾。连接断了不影响执行，重连按
`since=seq` 续拉；浏览器关闭照跑。

**与非功能修复的关系**：
- `_live` 持强引用（dict），修掉 `asyncio.create_task` 丢弃返回值可能被 GC 的隐患；
- `claim_run` 是 CAS 单写，跨实例也只有一个 worker 能拿到同一 run（防双跑）。

执行失败绝不静默：异常 → 落 error 事件 + run 置 failed。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Dict, Optional

from services.agent_zero.config import get_settings
from services.agent_zero.task_layer import run_service

logger = logging.getLogger(__name__)

CYCLE_INTERVAL_S = 1.0
# 运行级看门狗：**任何**正常等待都必须至少每 20s 有一条事件（LLM 有 keepalive 心跳、
# 工具执行有 ping、压缩有 60s 上限）。默认 **0 = 不中止**：后台任务常常跑一小时
# 以上（长思考 / 长工具参数 / 上游排队），用固定秒数一刀切会把正常的长任务判成
# 卡死并掐掉，误判远比漏判贵。需要硬上限时在 config 的 server/worker 段显式配置
# `run_event_stall_s`（>0 才启用）。
RUN_EVENT_STALL_S = 0.0

# 主循环轮询粒度（秒）：等待生成器事件时每隔这么久醒一次，用来检查
# 停止标记 / 自主执行墙钟 / 静默超时。0.5s 足够灵敏又不空转。
_TICK_S = 0.5
# worker 侧兜底心跳间隔（秒）：生成器长时间不产事件时由 worker 自己写一条
# ping，保证前端「仍在处理」可见、SSE 不被中间层掐断、停止按钮有反馈。
_HEARTBEAT_S = 20.0

# 停止兜底宽限期（秒）：发出取消后这么久仍未收尾，就强行从库里收尾为 stopped。
# 给同步阻塞段（线程池里的长工具 / 收尾落库）留一点时间，但用户不该等太久。
_STOP_GRACE_S = 3.0

# 兜底看门狗用独立线程：`loop.call_later` 的兜底只在事件循环**还能调度**时有效。
# 一旦某段同步代码把循环钉住（例如工具在线程里跑但主循环被 to_thread 前的
# 准备逻辑阻塞、或某个库内部同步等待），`call_later` 永远等不到机会执行，
# 界面就停在「处理中」——这正是用户说的「模型很长时间运行就会卡住，
# 必须停止再发起」。线程不受事件循环影响，到点直接改库。
_WATCHDOG_THREADS: Dict[str, threading.Timer] = {}
_WATCHDOG_LOCK = threading.Lock()


def _arm_stop_watchdog(run_id: str, delay: float = _STOP_GRACE_S) -> None:
    """挂一个线程级停止兜底（不依赖事件循环是否被钉住）。"""
    with _WATCHDOG_LOCK:
        old = _WATCHDOG_THREADS.pop(run_id, None)
        if old is not None:
            old.cancel()
        timer = threading.Timer(delay, _stop_watchdog_fire, args=(run_id,))
        timer.daemon = True
        _WATCHDOG_THREADS[run_id] = timer
        timer.start()


def _disarm_stop_watchdog(run_id: str) -> None:
    with _WATCHDOG_LOCK:
        timer = _WATCHDOG_THREADS.pop(run_id, None)
    if timer is not None:
        timer.cancel()


def _stop_watchdog_fire(run_id: str) -> None:
    """宽限期到：run 还没收尾就直接在库里收尾（线程里跑，不经事件循环）。"""
    with _WATCHDOG_LOCK:
        _WATCHDOG_THREADS.pop(run_id, None)
    try:
        run = run_service.get_run(run_id)
    except Exception:
        logger.debug("停止兜底读库失败 run=%s", run_id, exc_info=True)
        return
    if not run or run.get("status") in run_service.TERMINAL_STATUSES:
        return
    logger.warning("停止兜底（线程）触发：run=%s 在 %.0fs 内未收尾，强制结束",
                   run_id, _STOP_GRACE_S)
    silent = run_service.is_stop_silent(run_id)
    try:
        from services.agent_zero.task_layer import conversation

        node_id = conversation.persist_interrupted_run(
            run_id, outcome="stopped",
            error="" if silent else "运行已强制停止", silent=silent)
        if node_id and not silent:
            run_service.append_event(run_id, "interrupted", {
                "id": node_id, "outcome": "stopped",
                "error": "运行已强制停止"})
    except Exception:
        logger.exception("停止兜底（线程）落库失败 run=%s", run_id)
    try:
        run_service.finish_run(run_id, run_service.STATUS_STOPPED,
                               "" if silent else "运行已强制停止")
    except Exception:
        logger.exception("停止兜底（线程）收尾失败 run=%s", run_id)


def _stall_timeout() -> float:
    """运行级静默上限（秒，0 = 不中止）；优先读配置，其次用模块常量。"""
    try:
        from services.agent_zero.runtime.zero import llm as llm_mod

        return llm_mod.get_run_stall_timeout()
    except Exception:
        return RUN_EVENT_STALL_S


# delta / thinking 合并落库阈值：字符与时间双阈值。太小写库频繁，太大正文攒成
# 一大块，前端就一段一段跳。上游有些网关是按「一大段」吐字的（一次 100~300 字），
# 阈值必须明显小于一次吐字量，才能把它们切细；60ms 约等于一帧多，配合进程内总线
# 推送，观感接近逐 token 流，重连 replay 的块也更细。
# 前端另有「打字机」平滑（把一次到达的整段按帧展开），两层叠加后即使上游卡顿后
# 猛吐一大段，界面也是稳定推进而不是整块闪出。
TEXT_FLUSH_CHARS = 64
TEXT_FLUSH_MS = 60
_EVENT_KEYS = ("done", "error", "start", "delta", "thinking", "tool_call", "tool_result",
               "task_proposal", "plan", "context_compressed",
               "context_compress_start", "context_compress_done", "ping",
               "tool_approval", "approval_decided", "hook",
               "retry", "todo_update", "goal_update", "usage",
               "role_update", "org_update", "app_update", "schedule_update")


def _evt_kind(evt: Dict[str, Any]) -> str:
    for k in _EVENT_KEYS:
        if k in evt:
            return k
    return "event"


def autonomous_deadline(run: Dict[str, Any]) -> Optional[float]:
    """自主执行的墙钟截止时刻（monotonic）；非自主 / 不限时返回 None。

    自主 run 不受「回合数」约束；vibe coding 默认不设墙钟上限（配置为 0），
    需要时可在 Settings.autonomous_run_max_seconds 里临时加安全阀。
    """
    if (run or {}).get("mode") != "autonomous":
        return None
    limit = float(get_settings().autonomous_run_max_seconds or 0)
    return (time.monotonic() + limit) if limit > 0 else None


def _goal_after_failure(conv_id: str, run_id: str, error: str) -> None:
    """目标模式：失败也自动继续，入队一条诊断 / 重试 run。"""
    try:
        from services.agent_zero.task_layer import goal_service

        goal_service.after_run_failed(conv_id, run_id, error)
    except Exception:
        logger.exception("目标模式失败续跑处理失败 run=%s", run_id)


class ConversationRunWorker:
    def __init__(self) -> None:
        self._wake = asyncio.Event()
        # 持强引用（防 asyncio 只保弱引用导致任务被 GC）；键=run_id
        self._live: Dict[str, asyncio.Task] = {}
        self._started = False
        self._cycle_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # 失联 run 回收的节流时间戳（每个调度周期最多查一次库）
        self._last_reap = 0.0
        # 调度循环最后一次成功 tick 的墙钟时间（/healthz 用）
        self._last_cycle_at = 0.0

    # ── 对外 ────────────────────────────────────────────────

    def wake(self) -> None:
        self._wake.set()

    def force_stop(self, run_id: str) -> bool:
        """强制停止：直接取消该 run 的任务，不等下一个事件间隙。

        路由是同步函数（跑在线程池），所以通过事件循环线程安全地取消。
        没有存活任务（排队中 / 已结束）返回 False。

        **兜底**：取消只是把 CancelledError 投递进去；若生成器卡在无法取消的
        同步代码里（或取消被某层吞掉），run 会一直挂在 running。这里同时挂一个
        `_STOP_GRACE_S` 之后的看门狗，超时仍未收尾就直接在库里强制收尾，
        保证「点了停止就一定会停」。
        """
        task = self._live.get(run_id)
        if task is None or task.done():
            return False
        loop = self._loop
        # 后台工具（前台预算超时转过去的）不属于这条 task，必须显式杀掉：
        # 否则「点了停止」只停住对话循环，挂住的 shell 还在后台继续跑。
        try:
            from services.agent_zero.task_layer import run_service, tool_jobs

            run = run_service.get_run(run_id) or {}
            conv_id = str(run.get("conversation_id") or "")
            if conv_id:
                n = tool_jobs.kill_conversation(conv_id, reason="运行已停止")
                if n:
                    logger.info("停止 run=%s 时一并终止 %d 个后台工具", run_id, n)
        except Exception:
            logger.debug("终止后台工具失败（不影响停止）", exc_info=True)
        # 线程级兜底先挂上：事件循环若被同步代码钉住，call_later 永远不会触发
        # （界面就一直停在「处理中」，用户只能再点一次 / 重发消息）。
        _arm_stop_watchdog(run_id)
        try:
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(task.cancel)
                loop.call_soon_threadsafe(
                    lambda: loop.call_later(
                        _STOP_GRACE_S, self._force_finalize, run_id))
            else:
                task.cancel()
        except RuntimeError:
            return False
        return True

    def _cancel_task(self, run_id: str) -> None:
        """只投递取消，不再挂兜底（供兜底自身复用，避免无限重排）。"""
        task = self._live.get(run_id)
        if task is None or task.done():
            return
        loop = self._loop
        try:
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(task.cancel)
            else:
                task.cancel()
        except RuntimeError:
            pass

    def _force_finalize(self, run_id: str) -> None:
        """停止兜底：宽限期后仍活跃，就强行从库里收尾为 stopped。"""
        try:
            run = run_service.get_run(run_id)
        except Exception:
            logger.debug("停止兜底读库失败 run=%s", run_id, exc_info=True)
            return
        if not run or run.get("status") in run_service.TERMINAL_STATUSES:
            return
        logger.warning("停止兜底触发：run=%s 在 %.0fs 内未收尾，强制结束",
                       run_id, _STOP_GRACE_S)
        _silent = run_service.is_stop_silent(run_id)
        try:
            from services.agent_zero.task_layer import conversation

            node_id = conversation.persist_interrupted_run(
                run_id, outcome="stopped",
                error="" if _silent else "运行已强制停止", silent=_silent)
            if node_id and not _silent:
                run_service.append_event(run_id, "interrupted", {
                    "id": node_id, "outcome": "stopped",
                    "error": "运行已强制停止"})
        except Exception:
            logger.exception("停止兜底落库失败 run=%s", run_id)
        try:
            run_service.finish_run(run_id, run_service.STATUS_STOPPED,
                                   "运行已强制停止")
        except Exception:
            logger.exception("停止兜底收尾失败 run=%s", run_id)
        _disarm_stop_watchdog(run_id)
        # 任务句柄若还活着，再取消一次（可能卡在不响应取消的同步段里）。
        # 本函数可能跑在路由线程池里，必须经事件循环线程安全地投递。
        self._cancel_task(run_id)

    def stats(self) -> Dict[str, int]:
        try:
            queued = run_service.count_runs(status=run_service.STATUS_QUEUED)
            running = run_service.count_runs(status=run_service.STATUS_RUNNING)
        except Exception:
            queued = running = 0
        age = (round(time.time() - self._last_cycle_at, 1)
               if self._last_cycle_at else None)
        return {"queued": queued, "running": running,
                "live": len(self._live),
                "is_running": self._started,
                "last_cycle_at": self._last_cycle_at or None,
                "last_cycle_age_s": age,
                "slots_max": get_settings().max_concurrent_conversation_runs}

    # ── 循环 ────────────────────────────────────────────────

    async def _run_forever(self) -> None:
        while True:
            try:
                await self._cycle()
                self._last_cycle_at = time.time()
            except Exception:
                logger.exception("对话运行调度周期异常")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=CYCLE_INTERVAL_S)
            except asyncio.TimeoutError:
                pass

    def _reap_orphans(self) -> None:
        """回收「DB 里 running、但本进程没有任何任务持有」的 run。

        任务被强杀 / 收尾前抛异常时，run 会永远停在 running，该对话的队列
        随之永久堵死（前端表现为一直转圈、插队不执行）。这里每 15s 扫一次，
        把失联的 run 收尾为 failed 并落部分回复，队列即可继续。
        """
        now = time.monotonic()
        if now - self._last_reap < 15.0:
            return
        self._last_reap = now
        try:
            orphan_ids = run_service.orphan_running_runs(set(self._live))
        except Exception:
            logger.exception("扫描失联运行失败")
            return
        reason = "运行已失联（执行任务意外结束），已自动收尾"
        for rid in orphan_ids:
            try:
                if run_service.fail_orphan_run(rid, reason):
                    logger.warning("回收失联运行 run=%s", rid)
                    try:
                        from services.agent_zero.services.events import log_event

                        log_event("run.orphan_reaped", run_id=rid)
                    except Exception:
                        pass
            except Exception:
                logger.exception("回收失联运行失败 run=%s", rid)

    async def _cycle(self) -> None:
        self._reap_orphans()
        capacity = get_settings().max_concurrent_conversation_runs - len(self._live)
        if capacity <= 0:
            return
        try:
            queued = run_service.list_queued(limit=max(20, capacity * 2))
        except Exception:
            logger.exception("读取待跑对话运行失败")
            return
        # 同一对话：至多一条主持 run；成员并行 run 可同时跑，但有上限。
        # 已占用的对话不再无脑跳过，否则团队成员只能排队串行执行。
        coord_busy: set = set()
        member_counts: Dict[str, int] = {}
        try:
            for slot in run_service.running_slots():
                cid = str(slot.get("conversation_id") or "")
                if not cid:
                    continue
                if str(slot.get("target_role_id") or "").strip():
                    member_counts[cid] = member_counts.get(cid, 0) + 1
                else:
                    coord_busy.add(cid)
        except Exception:
            coord_busy, member_counts = set(), {}
        parallel_limit = run_service.parallel_member_limit()
        for run in queued:
            if capacity <= 0:
                break
            run_id = run["id"]
            if run_id in self._live:
                continue
            cid = run.get("conversation_id")
            target = str(run.get("target_role_id") or "").strip()
            if target:
                if member_counts.get(cid, 0) >= parallel_limit:
                    continue
            elif cid in coord_busy:
                continue
            self._start(run_id)
            if target:
                member_counts[cid] = member_counts.get(cid, 0) + 1
            else:
                coord_busy.add(cid)
            capacity -= 1

    def _start(self, run_id: str) -> None:
        task = asyncio.create_task(self._guarded_run(run_id))
        self._live[run_id] = task            # 持引用；done 回调里清理
        task.add_done_callback(lambda _t, rid=run_id: self._live.pop(rid, None))

    def _log_finish(self, run_id: str, started: float) -> None:
        """记录 run 结束事件（状态从库里读，避免每个分支都传参）。"""
        try:
            run = run_service.get_run(run_id) or {}
            from services.agent_zero.services.events import log_event

            log_event("run.finish", run_id=run_id,
                      conv_id=run.get("conversation_id"),
                      status=run.get("status"), mode=run.get("mode"),
                      duration_s=round(max(0.0, time.time() - started), 2))
        except Exception:
            pass

    async def _guarded_run(self, run_id: str) -> None:
        _t0 = time.time()
        # 运行期防睡眠：有 run 在跑时阻止系统空闲睡眠（macOS 用 caffeinate）。
        # 否则整机睡过去会把进程冻住十几分钟，用户看到的就是「卡住不动」。
        _power_on = False
        try:
            from services.agent_zero.task_layer import power

            power.acquire()
            _power_on = True
        except Exception:
            logger.debug("开启运行期防睡眠失败（忽略）", exc_info=True)
        try:
            await self._execute(run_id)
            self._log_finish(run_id, _t0)
        except asyncio.CancelledError:
            try:
                from services.agent_zero.task_layer import conversation, permissions

                permissions.cancel_pending_for_run(run_id)
                try:
                    from services.agent_zero.services import ask_service

                    ask_service.cancel_run(run_id)
                except Exception:
                    pass
                _silent = run_service.is_stop_silent(run_id)
                node_id = conversation.persist_interrupted_run(
                    run_id, outcome="stopped",
                    error="" if _silent else "运行已强制停止", silent=_silent)
                if node_id and not _silent:
                    run_service.append_event(run_id, "interrupted", {
                        "id": node_id, "outcome": "stopped",
                        "error": "运行已强制停止",
                    })
            except Exception:
                logger.exception("取消运行的回复落库失败 run=%s", run_id)
            run_service.finish_run(run_id, run_service.STATUS_STOPPED)
            self._log_finish(run_id, _t0)
            raise
        except Exception as e:
            logger.exception("对话运行 %s 未捕获异常", run_id)
            try:
                run_service.append_event(run_id, "error",
                                         {"error": f"运行异常：{type(e).__name__}: {e}"})
                run_service.finish_run(run_id, run_service.STATUS_FAILED,
                                       f"{type(e).__name__}: {e}")
            except Exception:
                logger.exception("对话运行 %s 异常收尾失败", run_id)
            self._log_finish(run_id, _t0)
        finally:
            _disarm_stop_watchdog(run_id)
            if _power_on:
                try:
                    power.release()
                except Exception:
                    logger.debug("关闭运行期防睡眠失败（忽略）", exc_info=True)

    # ── 单次执行 ────────────────────────────────────────────

    async def _execute(self, run_id: str) -> None:
        from services.agent_zero.task_layer import conversation

        run = run_service.get_run(run_id)
        if not run:
            return
        # CAS 认领：落后/重复调度在这里被挡住（跨实例安全）
        if not run_service.claim_run(run_id):
            return
        try:
            from services.agent_zero.services import ask_service

            ask_service.set_current_run(run_id)
        except Exception:
            pass
        try:
            from services.agent_zero.services.events import log_event

            log_event("run.start", run_id=run_id,
                      conv_id=run["conversation_id"], mode=run.get("mode"),
                      queue_pos=run.get("queue_pos"))
        except Exception:
            pass
        # 认领前后被请求停止：直接收尾，不启动 LLM
        if run_service.is_stop_requested(run_id):
            run_service.finish_run(run_id, run_service.STATUS_STOPPED)
            return

        conv_id = run["conversation_id"]
        conv = conversation.get_conversation(conv_id)
        if not conv:
            run_service.append_event(run_id, "error", {"error": f"对话不存在: {conv_id}"})
            run_service.finish_run(run_id, run_service.STATUS_FAILED, "对话不存在")
            return
        # 队列模式：本条 run 的排队消息此刻开始生效（进入历史 / 干预）
        if run.get("queued_msg_id"):
            try:
                conversation.mark_queued_message_active(str(run["queued_msg_id"]))
            except Exception:
                logger.exception("解除排队消息标记失败 run=%s", run_id)

        # 文件检查点：在真正执行前记录工作区状态（之后可回退）。失败不影响本轮。
        # **不阻塞发言角色上报**：检查点跑在线程池里，与「第一帧（start）+ 提示词
        # 准备」并行；进入事件循环、发出第一帧之后才 join 它，保证任何工具动手前
        # 快照已就绪。
        checkpoint_task: Optional[asyncio.Task] = None
        try:
            from services.agent_zero.services import file_checkpoint_service

            checkpoint_task = asyncio.ensure_future(asyncio.to_thread(
                file_checkpoint_service.create_for_run, run, conv))
        except Exception:
            logger.exception("创建文件检查点失败 run=%s", run_id)

        # 本轮选择的思考级别：ContextVar 隔离并发 run，主循环 / 子代理都会继承。
        try:
            from services.agent_zero.runtime.zero import llm as llm_mod

            llm_mod.set_reasoning_effort(run.get("thinking_level"))
            llm_mod.set_session_id(run.get("conversation_id") or run_id)
        except Exception:
            logger.debug("思考级别设置失败 run=%s", run_id, exc_info=True)

        buf: Dict[str, Any] = {"kind": None, "text": "", "evt": None, "since": 0.0}
        error_text: Optional[str] = None
        saw_done = False
        timed_out = False
        stalled = False
        usage: Dict[str, Any] = {}
        deadline = autonomous_deadline(run)
        events = conversation.generate_reply_stream(
            conv_id, run.get("created_by") or "system",
            content=run.get("content") or "",
            mentions=run.get("mentions") or None,
            target_role_id=run.get("target_role_id") or None,
            autonomous=(run.get("mode") == "autonomous"),
            auto_execute=bool(run.get("auto_execute")),
            run_id=run_id,
        ).__aiter__()
        # 预取任务：把「等下一个事件」从裸 await 变成可轮询的 task。
        # 裸 `await events.__anext__()` 会一直挂到生成器自己吐事件，期间：
        #   - 看不见 stop_requested（用户点停止要等下一个事件才生效）；
        #   - 无法在静默超时后收尾；
        #   - 无法响应自主执行的墙钟上限。
        # 用 task + 短 timeout 轮询解决，且**不取消**它（取消会向生成器内部注入
        # CancelledError，把正常运行掐死）；只有真的要停时才 cancel。
        prefetch: Optional[asyncio.Task] = None
        # 检查点还没收尾时，等它完成再落**后续**事件；第一帧（start = 谁在说话）
        # 不等它——用户先看到正确的发言角色，比先拍完快照重要。
        checkpoint_pending: Optional[asyncio.Task] = checkpoint_task
        silent_since = time.monotonic()
        next_stop_check = 0.0
        next_beat = time.monotonic() + _HEARTBEAT_S
        first_event_sent = False
        try:
            stall_s = _stall_timeout()
            while True:
                if prefetch is None:
                    prefetch = asyncio.ensure_future(events.__anext__())
                if checkpoint_pending is not None and first_event_sent:
                    # 第一帧（start = 谁在说话）已经落库下发；检查点在这里收尾。
                    # 此刻 prefetch 已经建好，生成器的提示词 / 历史准备与检查点
                    # 并行跑，谁快谁先，不再把「谁在说话」一起堵在后面。
                    try:
                        await checkpoint_pending
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("创建文件检查点失败 run=%s", run_id)
                    checkpoint_pending = None
                done, _pending = await asyncio.wait({prefetch}, timeout=_TICK_S)
                if not done:
                    # 这一个 tick 内没有事件：查停止 / 墙钟 / 静默，都不命中就继续等。
                    now = time.monotonic()
                    # 停止标记每秒查一次即可（每 tick 查库在并发 run 下没必要）
                    if now >= next_stop_check:
                        next_stop_check = now + 1.0
                        if run_service.is_stop_requested(run_id):
                            break
                    if deadline is not None and now > deadline:
                        timed_out = True
                        break
                    # 生成器长时间一条事件都不给（内部卡在一个不产事件的 await）：
                    # 由 worker 自己补心跳，保证前端「仍在处理」可见、SSE 不被中间层
                    # 掐断、用户点停止时界面有反应。只有真的静默到这个阈值才补。
                    if now >= next_beat:
                        next_beat = now + _HEARTBEAT_S
                        if (now - silent_since) >= _HEARTBEAT_S:
                            try:
                                run_service.append_event(
                                    run_id, "ping",
                                    {"ping": True,
                                     "elapsed_s": int(now - silent_since),
                                     "waiting": "worker", "phase": "silent"})
                            except Exception:
                                logger.debug("补心跳失败 run=%s", run_id,
                                             exc_info=True)
                    if stall_s > 0 and (now - silent_since) >= stall_s:
                        stalled = True
                        error_text = (f"运行 {int(stall_s)}s 没有任何事件"
                                      "（模型或工具无响应），已判定卡死并中止")
                        logger.warning("对话运行疑似卡死 run=%s conv=%s",
                                       run_id, conv_id)
                        break
                    continue
                try:
                    evt = prefetch.result()
                except StopAsyncIteration:
                    prefetch = None
                    break
                finally:
                    prefetch = None
                silent_since = time.monotonic()
                next_beat = silent_since + _HEARTBEAT_S
                # 已经拿到第一帧：下一轮循环开头会把检查点收掉（第一帧不等它）。
                first_event_sent = True
                if run_service.is_stop_requested(run_id):
                    break
                if deadline is not None and time.monotonic() > deadline:
                    timed_out = True
                    break
                kind = _evt_kind(evt)
                if kind in ("delta", "thinking"):
                    # 切换 kind（delta↔thinking）时必须先把上一种落库，
                    # 否则 _buffer_text 重置缓冲会把已积累的正文丢掉。
                    if buf["kind"] and buf["kind"] != kind:
                        self._flush(run_id, buf)
                    self._buffer_text(buf, kind, evt)
                    if (len(buf["text"]) >= TEXT_FLUSH_CHARS
                            or (time.monotonic() - buf["since"]) * 1000 >= TEXT_FLUSH_MS):
                        self._flush(run_id, buf)
                    continue
                self._flush(run_id, buf)
                if kind == "error":
                    error_text = str(evt.get("error") or "执行失败")
                elif kind == "done":
                    saw_done = True
                    usage = evt.get("usage") or {}
                run_service.append_event(run_id, kind, evt)
        finally:
            # 先落已经在缓冲里的正文，再关生成器：强制停止时外层会带着
            # CancelledError 走进这里，若先 await aclose() 而它又被取消/抛错，
            # 后面这行 flush 就执行不到，用户看到的就是「正文被吞了一截」。
            self._flush(run_id, buf)
            # 预取任务还挂着就先取消它：生成器此刻处于「正在运行」状态，
            # 直接 aclose() 会报 RuntimeError。取消时 CancelledError 会注入
            # 生成器内部的 await（正是停止要的效果：释放 HTTP / 杀工具进程）。
            if prefetch is not None and not prefetch.done():
                prefetch.cancel()
                try:
                    await prefetch
                except BaseException:      # noqa: BLE001 - 含 CancelledError / End
                    pass
            try:
                await events.aclose()
            except asyncio.CancelledError:
                raise          # 停止时的正常现象：正文已落库，取消继续向上传播
            except Exception:
                pass
            # 检查点还没被收尾（生成器一帧都没出 / 提前取消）：别把它的异常
            # 留成 "Task exception was never retrieved"。
            if checkpoint_pending is not None:
                if checkpoint_pending.done():
                    try:
                        checkpoint_pending.exception()
                    except BaseException:      # noqa: BLE001 - 已收尾，忽略
                        pass
                else:
                    checkpoint_pending.cancel()

        if stalled and error_text:
            try:
                run_service.append_event(run_id, "error", {"error": error_text})
            except Exception:
                logger.debug("写入卡死错误事件失败 run=%s", run_id, exc_info=True)

        # 停止 / 超时 / 报错但没有 done：把已经产生的正文、思考、工具轨迹落成
        # 一条助手消息。否则前端一刷新这条回复就消失，下一次还可能重复干活。
        interrupted_outcome = ""
        interrupted_error = ""
        if run_service.is_stop_requested(run_id):
            interrupted_outcome = "stopped"
            interrupted_error = "运行已停止"
        elif timed_out:
            interrupted_outcome = "failed"
            interrupted_error = "自主执行超时，已停止"
        elif error_text and not saw_done:
            interrupted_outcome = "failed"
            interrupted_error = error_text
        if interrupted_outcome:
            _silent = run_service.is_stop_silent(run_id)
            try:
                node_id = conversation.persist_interrupted_run(
                    run_id, outcome=interrupted_outcome,
                    error="" if _silent else interrupted_error, silent=_silent)
                if node_id and not _silent:
                    run_service.append_event(run_id, "interrupted", {
                        "id": node_id, "outcome": interrupted_outcome,
                        "error": interrupted_error,
                    })
            except Exception:
                logger.exception("中断回复落库失败 run=%s", run_id)

        final_status = run_service.STATUS_DONE
        if run_service.is_stop_requested(run_id):
            final_status = run_service.STATUS_STOPPED
        elif timed_out or (error_text and not saw_done):
            final_status = run_service.STATUS_FAILED
        # ── Stop 钩子（对齐 Claude Code）：run 收尾时通知，结果只落事件 ──
        if final_status != run_service.STATUS_STOPPED:
            try:
                from services.agent_zero.services import hooks_service

                _hrs = await hooks_service.stop(
                    str(conv.get("workspace_id") or ""), final_status,
                    run_id=run_id, session_id=conv_id)
                for _hr in _hrs:
                    _notable = (bool(_hr.get("output")) or bool(_hr.get("stderr"))
                                or bool(_hr.get("decision")) or not _hr.get("ok"))
                    if _notable:
                        run_service.append_event(run_id, "hook", {
                            "hook": {
                                "phase": "stop", "event": "Stop",
                                "command": _hr.get("command"),
                                "decision": _hr.get("decision"),
                                "reason": _hr.get("reason"),
                                "output": _hr.get("output"),
                                "stderr": _hr.get("stderr"),
                                "ok": _hr.get("ok"),
                                "timed_out": _hr.get("timed_out"),
                                "duration_ms": _hr.get("duration_ms"),
                            }})
            except Exception:
                logger.warning("Stop 钩子执行失败（已忽略） run=%s", run_id,
                               exc_info=True)

        if run_service.is_stop_requested(run_id):
            run_service.finish_run(run_id, run_service.STATUS_STOPPED)
        elif timed_out:
            run_service.append_event(
                run_id, "error",
                {"error": "自主执行超时，已停止；可缩小目标或拆分后重试"})
            run_service.finish_run(run_id, run_service.STATUS_FAILED, "autonomous_timeout")
            _goal_after_failure(conv_id, run_id, "自主执行超时，已停止")
        elif error_text and not saw_done:
            run_service.finish_run(run_id, run_service.STATUS_FAILED, error_text)
            _goal_after_failure(conv_id, run_id, str(error_text))
        else:
            run_service.finish_run(run_id, run_service.STATUS_DONE)
            # 首轮回复完成：异步生成 AI 标题（不阻塞、失败静默保留占位标题）
            try:
                from services.agent_zero.task_layer import title_service

                title_service.schedule(conv_id)
            except Exception:
                logger.debug("排会话标题生成失败 run=%s", run_id, exc_info=True)
            # 目标模式：run 正常收尾后记账；目标仍 active 则自动入队续跑
            try:
                from services.agent_zero.task_layer import goal_service

                goal_service.after_run_finished(conv_id, run_id, usage)
            except Exception:
                logger.exception("目标模式续跑处理失败 run=%s", run_id)

        # 群组后台任务：成员 run 收尾后，若整个任务已结束，写一条完成触发器，
        # 并由任务层把结果自动交回主会话（完成 / 失败都继续）。
        try:
            from services.agent_zero.services import team_task_service

            team_task_service.on_run_finished(run_id)
        except Exception:
            logger.debug("后台任务收尾通知失败 run=%s", run_id, exc_info=True)

    # ── 文本合并 ────────────────────────────────────────────

    @staticmethod
    def _buffer_text(buf: Dict[str, Any], kind: str, evt: Dict[str, Any]) -> None:
        if buf["kind"] != kind:
            buf["kind"] = kind
            buf["text"] = ""
            # 保留 role_id / role_name / round 等元信息，只把正文换掉
            buf["evt"] = {k: v for k, v in evt.items() if k not in ("delta", "thinking")}
            buf["since"] = time.monotonic()      # 时间阈值从这段起点算
        buf["text"] += str(evt.get(kind) or "")

    @staticmethod
    def _flush(run_id: str, buf: Dict[str, Any]) -> None:
        if not buf.get("kind") or not buf.get("text"):
            buf["kind"], buf["text"], buf["evt"], buf["since"] = None, "", None, 0.0
            return
        payload = dict(buf.get("evt") or {})
        payload[buf["kind"]] = buf["text"]
        try:
            run_service.append_event(run_id, buf["kind"], payload)
        except Exception:
            logger.debug("对话事件落库失败 run=%s kind=%s", run_id, buf["kind"])
        buf["kind"], buf["text"], buf["evt"], buf["since"] = None, "", None, 0.0


_worker: Optional[ConversationRunWorker] = None


def get_worker() -> ConversationRunWorker:
    global _worker
    if _worker is None:
        _worker = ConversationRunWorker()
    return _worker


def start_worker() -> asyncio.Task:
    """启动对话运行 worker（main lifespan 调用；返回任务句柄供取消）。"""
    w = get_worker()
    if w._started:
        return w._cycle_task  # type: ignore[return-value]
    w._started = True
    w._loop = asyncio.get_running_loop()
    # 启动恢复：上一进程遗留的 running run 已无执行者，收尾为失败（否则永远「进行中」）
    try:
        n = run_service.recover_running_runs()
        if n:
            logger.warning("对话运行启动恢复：%d 条中断的运行已收尾为失败", n)
    except Exception:
        logger.exception("对话运行启动恢复失败（不影响启动）")
    w._cycle_task = asyncio.create_task(w._run_forever())
    return w._cycle_task


async def request_run(run_id: str) -> None:
    """立即触发一次调度（创建 run 后调用）。"""
    get_worker().wake()
