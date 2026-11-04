"""AtlasX FastAPI 应用工厂与服务启动（CLI / macOS 桌面端共用）。

桌面端不经过 `main-agent-zero.py`（文件名带连字符，无法直接 import），
因此把「建 app / 起 uvicorn」抽到这里：
- `create_app()`：路由、静态前端、lifespan（数据库初始化、worker、恢复运行）；
- `serve()`：绑定端口（被占用自动顺延）、写 runtime.json、起 uvicorn；
  支持 `on_listening(url)` / `on_server(server)` 回调，桌面端据此在服务
  就绪后打开原生窗口，并在关窗时优雅退出。
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, Optional, Tuple

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from services.shared.log_file import SelfHealingTimedRotatingFileHandler
from services.shared.log_level import apply_log_level_from_config
from services.shared.logging_filters import attach_heartbeat_log_filter
from services.shared.server_bind import bind_socket
from services.shared.yaml_config import load_service_config
from services.agent_zero.database import init_database
from services.agent_zero.version import APP_NAME, APP_VERSION
from services.agent_zero.routes import (
    app_routes,
    avatar_routes,
    department_routes,
    role_routes,
    task_routes,
    team_routes,
    work_routes,
    workspace_admin_routes,
    workspace_routes,
)
from services.agent_zero.stores import zero_store

logger = logging.getLogger(__name__)

API_PREFIX = "/api/agent-zero"


class _JsonFormatter(logging.Formatter):
    """一行一个 JSON：文本日志的可读性留给 agent_zero.log，这里给机器读。"""

    def format(self, record: logging.LogRecord) -> str:
        data: Dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        event = getattr(record, "event", None)
        if event:
            data["event"] = event
        fields = getattr(record, "event_fields", None)
        if isinstance(fields, dict):
            data.update(fields)
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, default=str)


def setup_logging(logs_dir: str, config_yaml: str) -> None:
    """文件日志（按天滚动 + 心跳过滤），重复调用不会重复挂 handler。"""
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.abspath(os.path.join(logs_dir, "agent_zero.log"))
    json_path = os.path.abspath(os.path.join(logs_dir, "agent_zero.jsonl"))
    root = logging.getLogger()
    has_text = has_json = False
    for h in root.handlers:
        if isinstance(h, logging.FileHandler):
            try:
                name = os.path.abspath(h.baseFilename)
                if name == log_path:
                    has_text = True
                elif name == json_path:
                    has_json = True
            except Exception:
                continue
    if not has_text:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        handler = SelfHealingTimedRotatingFileHandler(
            log_path, when="midnight", interval=1, backupCount=5, encoding="utf-8")
        handler.setFormatter(formatter)
        attach_heartbeat_log_filter(handler)
        root.addHandler(handler)
    if not has_json:
        jh = SelfHealingTimedRotatingFileHandler(
            json_path, when="midnight", interval=1, backupCount=5, encoding="utf-8")
        jh.setFormatter(_JsonFormatter())
        root.addHandler(jh)
    apply_log_level_from_config(config_yaml)


def _server_config(config_yaml: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    cfg = load_service_config(config_yaml) or {}
    server = (cfg.get("server") or {}) if isinstance(cfg, dict) else {}
    return cfg, server


def create_app(*, config_yaml: str, web_dir: str, logs_dir: str) -> FastAPI:
    setup_logging(logs_dir, config_yaml)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("%s 启动中...", APP_NAME)
        try:
            from services.agent_zero.services import backup_service

            pre = backup_service.preflight()
            if pre.get("restored"):
                logger.warning("数据库损坏，已从备份恢复: %s", pre["restored"].get("name"))
            backup_service.ensure_daily_backup()
        except Exception:
            logger.exception("数据库备份/完整性检查失败（继续启动）")

        try:
            init_database()
            zero_store.ensure_defaults()
        except Exception:
            logger.exception("数据库初始化失败")
            raise

        try:
            from services.agent_zero.services import team_service

            n = team_service.cleanup_phantom_default_teams()
            if n:
                logger.info("清掉 %d 个旧版自动归一的「默认群聊」（单聊不再成群聊）", n)
        except Exception as e:
            logger.warning("清理旧「默认群聊」失败（不影响启动）: %s", e)

        try:
            from services.agent_zero.task_layer import conversation as conv_layer

            n = conv_layer.archive_stale_conversations()
            if n:
                logger.info("自动归档 %d 条 30 天无活动的对话", n)
        except Exception as e:
            logger.warning("对话自动归档扫描失败: %s", e)

        try:
            from services.agent_zero.task_layer import run_service

            n = run_service.recover_running_runs()
            if n:
                logger.warning("已回收 %s 个中断的对话运行", n)
        except Exception:
            logger.exception("回收中断运行失败")

        try:
            from services.agent_zero.task_layer import permissions

            n = permissions.expire_orphan_approvals()
            if n:
                logger.warning("已关闭 %s 个未完成的审批（进程重启）", n)
        except Exception:
            logger.exception("清理未完成审批失败")

        conv_worker_task = None
        try:
            from services.agent_zero.task_layer.conv_worker import start_worker

            conv_worker_task = start_worker()
            logger.info("对话运行 worker 已启动")
        except Exception:
            logger.exception("对话运行 worker 启动失败")

        try:
            from services.agent_zero.task_layer import run_service

            _pruned = run_service.prune_history()
            if _pruned:
                logger.info("已清理 %d 条过期运行记录（含事件）", _pruned)
        except Exception:
            logger.warning("清理过期运行记录失败（不影响启动）", exc_info=True)

        try:
            from services.agent_zero.stores import settings_store

            settings_store.migrate_plaintext_api_keys()
        except Exception:
            logger.debug("API key 迁移到 Keychain 失败", exc_info=True)

        try:
            from services.agent_zero.services.events import log_event

            log_event("app.start", version=APP_VERSION)
        except Exception:
            pass

        logger.info("%s 就绪（本地单用户 / sqlite）", APP_NAME)
        yield

        if conv_worker_task:
            try:
                conv_worker_task.cancel()
                import asyncio

                try:
                    await conv_worker_task
                except asyncio.CancelledError:
                    pass
            except Exception:
                logger.warning("对话运行 worker 停止失败", exc_info=True)
        try:
            from services.agent_zero.runtime.zero import llm as llm_mod

            await llm_mod.close_llm_clients()
        except Exception:
            logger.debug("关闭 LLM 连接池失败", exc_info=True)
        try:
            from services.agent_zero.services.events import log_event

            log_event("app.stop", version=APP_VERSION)
        except Exception:
            pass
        logger.info("%s 已关闭", APP_NAME)

    app = FastAPI(
        title=APP_NAME,
        description="本地单用户 Agent（sqlite / 静态前端）",
        version=APP_VERSION,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.config_yaml = config_yaml
    app.state.web_dir = web_dir

    app.include_router(app_routes.router, prefix=API_PREFIX, tags=["应用"])
    app.include_router(task_routes.router, prefix=API_PREFIX, tags=["对话"])
    app.include_router(work_routes.router, prefix=API_PREFIX, tags=["工单助手"])
    app.include_router(workspace_admin_routes.router, prefix=API_PREFIX, tags=["工作区"])
    app.include_router(team_routes.router, prefix=API_PREFIX, tags=["团队"])
    app.include_router(department_routes.router, prefix=API_PREFIX, tags=["部门"])
    app.include_router(avatar_routes.router, prefix=API_PREFIX, tags=["头像"])
    app.include_router(role_routes.router, prefix=API_PREFIX, tags=["角色"])
    app.include_router(workspace_routes.router,
                       prefix=f"{API_PREFIX}/workspace", tags=["Workspace"])

    @app.get("/health")
    def health_check():
        return {"status": "ok", "service": "agent-zero"}

    @app.get("/healthz")
    def healthz_check():
        """深健康检查：DB 可读 + worker 心跳 + 队列。失败返回 503。"""
        from services.agent_zero.services import metrics_service

        data = metrics_service.health()
        return JSONResponse(data, status_code=200 if data.get("ok") else 503)

    assets_dir = os.path.join(web_dir, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    # V3 新界面（工作区 / 角色 / 群组独立 + 悬浮窗）：旧界面仍保留在 /
    v3_dir = os.path.join(web_dir, "v3")
    if os.path.isdir(v3_dir):
        @app.get("/v3", include_in_schema=False)
        def v3_index():
            return FileResponse(os.path.join(v3_dir, "index.html"))

        app.mount("/v3", StaticFiles(directory=v3_dir, html=True), name="v3")

    # V3.2：默认入口切到新壳；旧界面保留在 /classic，方便还没迁移的设置页。
    v3_index = os.path.join(web_dir, "v3", "index.html")

    @app.get("/", include_in_schema=False)
    def index_page():
        if os.path.exists(v3_index):
            return FileResponse(v3_index)
        return FileResponse(os.path.join(web_dir, "index.html"))

    @app.get("/classic", include_in_schema=False)
    def classic_page(request: Request):
        # 仅用于内嵌 V2 设置（?settings=1）；直接访问旧界面仍然 410。
        if not request.query_params.get("settings"):
            return JSONResponse(status_code=410, content={
                "detail": "旧界面已移除；仅 /classic?settings=1 作为设置页内嵌。",
            })
        return FileResponse(os.path.join(web_dir, "index.html"))

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon():
        return FileResponse(os.path.join(web_dir, "favicon.svg"))

    return app


_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def _public_host(host: str) -> str:
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


def write_runtime(runtime_file: str, host: str, port: int) -> str:
    url = f"http://{_public_host(host)}:{port}/"
    try:
        os.makedirs(os.path.dirname(runtime_file), exist_ok=True)
        with open(runtime_file, "w", encoding="utf-8") as f:
            json.dump({
                "pid": os.getpid(),
                "host": host,
                "port": port,
                "url": url,
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.warning("写入 runtime.json 失败", exc_info=True)
    return url


def serve(
    app: FastAPI,
    *,
    config_yaml: str,
    runtime_file: str,
    on_listening: Optional[Callable[[str], None]] = None,
    on_server: Optional[Callable[[uvicorn.Server], None]] = None,
) -> None:
    """绑定端口并启动 uvicorn（阻塞）。

    端口规则见 `services.shared.server_bind`：默认端口被占用时自动顺延。
    `on_listening(url)` 在 socket 绑定后、uvicorn 启动前调用（桌面端用来拿 URL）。
    `on_server(server)` 暴露 uvicorn Server，桌面端关窗时设 `should_exit=True`。
    """
    cfg, server_cfg = _server_config(config_yaml)
    host = str(server_cfg.get("host") or "127.0.0.1")
    preferred = int(server_cfg.get("port") or 10210)
    auto_port = bool(server_cfg.get("auto_port", True))
    tries = int(server_cfg.get("port_tries") or 30)

    if host not in _LOOPBACK_HOSTS:
        logger.warning(
            "服务监听非回环地址 %s：接口**没有鉴权**，/workspace、/conversations 等"
            "（含写文件 / 删文件）将对同网段可见。确认这是你要的。", host,
        )

    sock, port = bind_socket(host, preferred, auto=auto_port, tries=tries)
    url = write_runtime(runtime_file, host, port)
    logger.info("监听 %s:%s → %s", host, port, url)
    print(f"  ✓ {APP_NAME} 已启动: {url}", flush=True)
    if on_listening:
        on_listening(url)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        reload=False,
        access_log=False,
        log_level=str((cfg.get("logging") or {}).get("log_level") or "info").lower(),
    )
    server = uvicorn.Server(config)
    if on_server:
        on_server(server)
    server.run(sockets=[sock])
