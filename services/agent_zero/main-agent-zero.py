"""AtlasX 本地服务入口。

- 前端：`frontend/`（Vue 3 + Element Plus，构建到 `web/dist`，唯一入口 `/`）
- API：`/api/agent-zero`
- 数据库：`data/agent_zero/agent_zero.db`（sqlite，唯一数据源）
- 端口：默认 10210；被占用时按配置自动顺延

应用工厂与 uvicorn 启动在 `services/agent_zero/webapp.py`，
macOS 桌面端（desktop/launcher.py）复用同一份实现。
"""

import importlib.util
import os

_PB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "platform_bootstrap.py")
_spec = importlib.util.spec_from_file_location("_platform_bootstrap", _PB)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_ROOT, _PROJECT_ROOT = _mod.bootstrap(__file__)

_CONFIG_YAML = os.environ.get("AI_PLATFORM_CONFIG_YAML") or os.path.join(
    _PROJECT_ROOT, "config", "agent_zero", "config.yaml"
)
_WEB_DIR = os.environ.get("AGENT_ZERO_WEB_DIR") or os.path.join(_PROJECT_ROOT, "web")
_LOGS_DIR = os.environ.get("AGENT_ZERO_LOGS_DIR") or os.path.join(_PROJECT_ROOT, "logs")
_RUNTIME_FILE = os.path.join(_PROJECT_ROOT, "data", "agent_zero", "runtime.json")


def main() -> None:
    from services.agent_zero.webapp import create_app, serve

    app = create_app(config_yaml=_CONFIG_YAML, web_dir=_WEB_DIR, logs_dir=_LOGS_DIR)
    serve(app, config_yaml=_CONFIG_YAML, runtime_file=_RUNTIME_FILE)


if __name__ == "__main__":
    main()
