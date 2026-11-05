"""工具后台监督 + 沙箱挂死修复 + 进程面板的回归测试。

对应 2026-10-29 现场：一条 `bash build_macos.sh | tail -25` 的 tool_call
在库里挂了 40 分钟，run 期间零事件，用户只能点停止。三处根因：

1. `conversation._stream_role` 里 `while not tool_task.done()` 无条件等工具；
2. `SubprocessSandbox` 用 `communicate()` / `wait()`，进程退出后仍等管道 EOF；
3. 前端活动条 90s 后不再刷新，看着像死了。

这里锁住 1、2 的修法以及进程面板的接口契约，防止回退。
"""

from __future__ import annotations

import asyncio
import inspect
import time

from services.agent_zero.task_layer import tool_jobs


# ── 后台任务注册表 ─────────────────────────────────────────────────────


def test_adopt_and_handle_lifecycle():
    """adopt 的任务能跑完、能查状态、能读结果。"""

    async def scenario():
        async def work():
            await asyncio.sleep(0.05)
            return '{"ok": true, "out": "done"}'

        job = tool_jobs.adopt(conversation_id="conv-t1", run_id="", name="shell",
                              args={"cmd": "echo hi"},
                              task=asyncio.ensure_future(work()), call_id="call-1")
        assert job["status"] == "running"

        mid = await tool_jobs.handle("conv-t1", {"action": "status", "job_id": job["id"]})
        assert mid["status"] == "running"

        done = await tool_jobs.handle("conv-t1", {"action": "wait", "job_id": job["id"],
                                                  "timeout_s": 5})
        assert done["status"] == "done"
        assert "done" in done["result"]

        listed = await tool_jobs.handle("conv-t1", {"action": "list"})
        assert [j["job_id"] for j in listed["jobs"]] == [job["id"]]
        assert listed["running"] == 0

    asyncio.run(scenario())


def test_failure_is_recorded_not_swallowed():
    """任务抛异常必须落成 failed + error，不能静默消失。"""

    async def scenario():
        async def boom():
            await asyncio.sleep(0.02)
            raise RuntimeError("failed-here")

        job = tool_jobs.adopt(conversation_id="conv-t2", run_id="",
                              name="read_file", task=asyncio.ensure_future(boom()))
        await asyncio.sleep(0.3)
        got = tool_jobs.get(job["id"])
        assert got["status"] == "failed"
        assert "failed-here" in got["error"]

    asyncio.run(scenario())


def test_kill_marks_killed_and_clears_running():
    """kill 之后状态是 killed，且不再计入 running（停止运行会用到）。"""

    async def scenario():
        async def slow():
            await asyncio.sleep(30)

        job = tool_jobs.adopt(conversation_id="conv-t3", run_id="",
                              name="shell", task=asyncio.ensure_future(slow()))
        await asyncio.sleep(0.05)
        assert tool_jobs.running_count("conv-t3") == 1
        assert tool_jobs.kill(job["id"]) is True
        await asyncio.sleep(0.1)
        assert tool_jobs.get(job["id"])["status"] == "killed"
        assert tool_jobs.running_count("conv-t3") == 0
        assert tool_jobs.kill(job["id"]) is False       # 已结束的不能重复 kill

    asyncio.run(scenario())


def test_jobs_are_isolated_per_conversation():
    """跨对话不能读别人的后台任务（避免上下文串台）。"""

    async def scenario():
        async def slow():
            await asyncio.sleep(30)

        job = tool_jobs.adopt(conversation_id="conv-a", run_id="",
                              name="shell", task=asyncio.ensure_future(slow()))
        out = await tool_jobs.handle("conv-b", {"action": "result", "job_id": job["id"]})
        assert "error" in out
        assert tool_jobs.list_jobs("conv-b") == []
        tool_jobs.kill(job["id"])

    asyncio.run(scenario())


def test_unknown_action_and_job_report_errors():
    async def scenario():
        out = await tool_jobs.handle("conv-t4", {"action": "nope", "job_id": "x"})
        assert "error" in out
        out2 = await tool_jobs.handle("conv-t4", {"action": "status", "job_id": "job-none"})
        assert "error" in out2

    asyncio.run(scenario())


def test_foreground_budget_and_idle_timeout_are_configurable():
    """两个新配置项要能从 config.yaml 读出来（0 = 关闭）。"""
    from services.agent_zero.runtime.zero.dispatch import ToolDispatcher

    assert ToolDispatcher.foreground_budget() > 0
    assert ToolDispatcher.shell_idle_timeout() > 0


def test_conversation_no_longer_awaits_tool_forever():
    """对话层必须存在「超预算转后台」的分支，否则慢工具会再挂住整轮。"""
    from services.agent_zero.task_layer import conversation

    src = inspect.getsource(conversation)
    # 串行路径用 tool_jobs.adopt，并行路径用别名 _tjobs.adopt：两处都必须存在，
    # 否则有一条路径仍会无限等待慢工具。
    assert "tool_jobs.adopt(" in src, "缺少把超预算工具转后台的逻辑（串行路径）"
    assert "_tjobs.adopt(" in src, "并行路径也要把超预算工具转后台"
    assert "foreground_budget()" in src, "缺少前台预算读取"


def test_adopted_flag_is_initialized_before_tool_branches():
    """`_adopted` 必须在工具分支之前初始化。

    2026-10-29 现场：模型调用 todo / goal_report 等工具时，
    这些调用走独立分支，不经过「超预算转后台」的 else 逻辑；收尾处读
    `_adopted` 抛 UnboundLocalError，整轮对话直接中断。
    """
    from services.agent_zero.task_layer import conversation

    src = inspect.getsource(conversation)
    init_at = src.index("_adopted = False")
    for marker in ('elif name in ("todo_write", "todo")',
                   'elif name == "goal_report"',
                   'elif name == "team_manage"'):
        assert init_at < src.index(marker), f"{marker} 之前没有初始化 _adopted"
    assert init_at < src.index("_bg_id = "), "读取 _adopted 之前必须已初始化"


def test_cap_sleep_never_rewrites_command():
    """长等待只提示、不改写命令（2026-11-02 修）。

    旧实现把 sleep 时长截到 120s："sleep 300 && ./deploy.sh" 会变成
    "sleep 120 && ./deploy.sh"，后半段提前 180s 执行 —— 定时脚本因此跑在错误的
    时间点（用户反馈「定时的脚本时间超过 120s」）。现在是原样执行 + 提示。
    """
    from services.agent_zero.runtime.zero.dispatch import ToolDispatcher

    src = "sleep" + " 300 && echo done"
    cmd, notes = ToolDispatcher.cap_sleep(src)
    assert cmd == src, "命令不能被改写"
    assert notes and "300" in notes[0]

    src = "sleep" + " 5m"
    cmd, notes = ToolDispatcher.cap_sleep(src)
    assert cmd == src and notes

    src = "sleep" + " 30 && echo ok"
    assert ToolDispatcher.cap_sleep(src) == (src, [])

    src = "Start-" + "Sleep -Seconds 300"
    cmd, notes = ToolDispatcher.cap_sleep(src)
    assert cmd == src and notes

    # 不是 shell 的 sleep 调用（如 Python 代码）不要误判
    src = "python3 -c " + chr(39) + "import time; time.sleep(300)" + chr(39)
    assert ToolDispatcher.cap_sleep(src) == (src, [])


def test_tool_job_tool_is_registered_and_exposed():
    """tool_job 必须注册 + 能下发给模型，否则模型根本没有监督手段。

    2026-10 起命令类工具（shell / tool_job / vcs / codegraph）不再折进 code 包，
    改为独立工具直接下发，所以这里是「挂包 或 独立下发」二者之一。
    """
    from services.agent_zero.tools import packs, registry

    assert registry.get_tool("tool_job") is not None
    exposed = ({code for pack in packs.PACKS for code in pack.ops.values()}
               | set(packs.unpacked_codes(registry.known_codes())))
    assert "tool_job" in exposed


def test_stop_also_kills_background_jobs():
    """停止 run 必须连带终止它的后台工具，否则「停不下来」。"""
    from services.agent_zero.task_layer import conv_worker

    src = inspect.getsource(conv_worker.ConversationRunWorker.force_stop)
    assert "kill_conversation" in src


def test_run_sets_power_assertion():
    """run 执行期间必须申请防睡眠，结束必须释放（否则长任务被系统冻结）。"""
    from services.agent_zero.task_layer import conv_worker, power

    src = inspect.getsource(conv_worker.ConversationRunWorker._guarded_run)
    assert "power.acquire()" in src and "power.release()" in src
    assert callable(power.acquire) and callable(power.release)
    assert set(power.status()) >= {"active_runs", "sleep_blocked"}


# ── 沙箱：管道死锁与空闲兜底 ───────────────────────────────────────────


def _sb_mod():
    from services.agent_zero.runtime.zero.sandbox import subprocess_sandbox

    return subprocess_sandbox


def test_sandbox_avoids_pipe_eof_deadlock():
    """判定进程退出不能依赖管道 EOF（后台子进程会拖住整条命令）。"""
    src = inspect.getsource(_sb_mod())
    assert "process_exit_code" in src
    assert "proc.communicate()" not in src, "communicate() 会等管道 EOF，正是挂死根因"


def test_shell_returns_when_child_holds_pipe(tmp_path):
    """子进程继承 stdout 时，命令必须在收尾窗口内返回而不是永远挂着。"""

    async def scenario():
        sandbox = _sb_mod().SubprocessSandbox(tmp_path, timeout_s=0)
        t0 = time.monotonic()
        r = await sandbox.run_shell("sleep 30 & echo launched")
        elapsed = time.monotonic() - t0
        assert elapsed < 20, f"仍在等管道 EOF：{elapsed:.1f}s"
        assert "launched" in r.stdout
        assert r.pipe_stalled is True

    asyncio.run(scenario())


def test_shell_idle_timeout_kills_silent_hang(tmp_path):
    """零输出挂住的命令必须被空闲兜底收掉。"""

    async def scenario():
        sandbox = _sb_mod().SubprocessSandbox(tmp_path, timeout_s=0, idle_timeout_s=1.5)
        t0 = time.monotonic()
        r = await sandbox.run_shell("sleep 30")
        elapsed = time.monotonic() - t0
        assert elapsed < 20
        assert r.timed_out is True
        assert "没有任何输出" in r.stderr

    asyncio.run(scenario())


def test_shell_keeps_long_outputting_command_alive(tmp_path):
    """有输出的长命令不能被误杀（构建要能跑完）。"""

    async def scenario():
        sandbox = _sb_mod().SubprocessSandbox(tmp_path, timeout_s=0, idle_timeout_s=2)
        r = await sandbox.run_shell("for i in 1 2 3 4; do echo tick$i; sleep 0.5; done")
        assert r.ok is True
        assert r.stdout.count("tick") == 4

    asyncio.run(scenario())


# ── 进程面板接口 ───────────────────────────────────────────────────────


def test_process_routes_list_and_stop_single(monkeypatch):
    """进程面板：能列出后台工具/子代理，且停一个不影响另一个。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from services.agent_zero.routes import task_routes
    from services.agent_zero.task_layer import conversation as convmod, tool_activity

    app = FastAPI()
    app.include_router(task_routes.router)
    client = TestClient(app)

    async def scenario():
        async def slow():
            await asyncio.sleep(60)

        loop = asyncio.get_running_loop()
        j1 = tool_jobs.adopt(conversation_id="conv-proc", run_id="", name="shell",
                             args={"cmd": "bash build.sh | tail -25"},
                             task=loop.create_task(slow()))
        j2 = tool_jobs.adopt(conversation_id="conv-proc", run_id="", name="call_subagent",
                             args={"goal": "refactor"}, task=loop.create_task(slow()))
        # 还在前台预算内、没有被转后台的 shell：进程面板也必须看得见。
        live = tool_activity.begin(conversation_id="conv-proc", name="shell",
                                   args={"cmd": "pytest -q"}, call_id="call-live")
        live_slow = loop.create_task(slow())
        tool_activity.attach(live, live_slow)
        live_sub = tool_activity.begin(conversation_id="conv-proc",
                                       name="call_subagent",
                                       args={"goal": "review"}, call_id="call-live-sub")

        orig = convmod.get_conversation
        monkeypatch.setattr(task_routes, "conversation", convmod)
        monkeypatch.setattr(convmod, "get_conversation",
                            lambda cid: ({"id": cid} if cid == "conv-proc" else None))
        # 即使有活动 run，进程面板也只列工具/子代理，不再重复一条「对话运行」。
        monkeypatch.setattr(task_routes.run_service, "running_run",
                            lambda cid: {"id": "run-fake", "conversation_id": cid,
                                         "status": "running"})
        try:
            r = client.get("/conversations/conv-proc/processes")
            assert r.status_code == 200
            items = r.json()["data"]["items"]
            kinds = {i["kind"] for i in items}
            assert "tool" in kinds and "subagent" in kinds
            assert "run" not in kinds
            # 命令行摘要要能看见（用户靠它判断该不该停）
            tool_row = next(i for i in items if i["kind"] == "tool")
            assert "build.sh" in tool_row["args"].get("cmd", "")
            live_row = next(i for i in items if i["id"] == live)
            assert live_row["args"]["cmd"] == "pytest -q"
            assert live_row["stoppable"] is True
            assert next(i for i in items if i["id"] == live_sub)["kind"] == "subagent"
            assert next(i for i in items if i["id"] == j1["id"])["stoppable"] is True

            assert client.get("/conversations/nope/processes").status_code == 404

            r2 = client.post(f"/processes/{j1['id']}/stop")
            assert r2.status_code == 200
            assert r2.json()["data"]["stopped"] is True
            await asyncio.sleep(0.3)
            assert tool_jobs.get(j1["id"])["status"] == "killed"
            # 另一个进程不受影响 —— 这正是「停单进程不影响会话」的核心保证
            assert tool_jobs.get(j2["id"])["status"] == "running"

            assert client.post("/processes/job-none/stop").status_code == 404

            # 前台工具也能从进程面板单独停：只取消这次调用，不动会话
            r3 = client.post(f"/processes/{live}/stop")
            assert r3.status_code == 200
            assert r3.json()["data"]["stopped"] is True
            await asyncio.sleep(0.1)
            assert live_slow.cancelled() is True
        finally:
            monkeypatch.setattr(convmod, "get_conversation", orig)
            tool_activity.end(live)
            tool_activity.end(live_sub)
            tool_jobs.kill(j2["id"])

    asyncio.run(scenario())


def test_tool_activity_tracks_foreground_and_clears():
    """前台工具登记：开始可见、结束消失、运行收尾统一清理。"""
    from services.agent_zero.task_layer import tool_activity

    a = tool_activity.begin(conversation_id="conv-live", run_id="run-1",
                            name="shell", args={"cmd": "sleep 5"}, call_id="c1")
    b = tool_activity.begin(conversation_id="conv-live", run_id="run-1",
                            name="call_team", args={"member_name": "dev"},
                            call_id="c2")
    c = tool_activity.begin(conversation_id="conv-other", name="read_file",
                            args={"path": "a.py"})

    rows = tool_activity.list_active("conv-live")
    assert {r["id"] for r in rows} == {a, b}
    assert {r["kind"] for r in rows} == {"tool", "subagent"}
    assert all(r["stoppable"] is True for r in rows)
    assert len(tool_activity.list_active("conv-live")) == 2

    tool_activity.end(a)
    assert [r["id"] for r in tool_activity.list_active("conv-live")] == [b]

    # 运行收尾兜底：该会话残项全清，其它会话不受影响
    tool_activity.clear_conversation("conv-live")
    assert tool_activity.list_active("conv-live") == []
    assert [r["id"] for r in tool_activity.list_active("conv-other")] == [c]
    tool_activity.end(c)
