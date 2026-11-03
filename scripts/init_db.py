"""初始化 AtlasX 独立部署数据库（默认 sqlite）。

用法（项目根目录）：
    .venv/bin/python scripts/init_db.py
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "services", "agent_zero"))
sys.path.insert(0, ROOT)
os.environ.setdefault(
    "AI_PLATFORM_CONFIG_YAML", os.path.join(ROOT, "config", "agent_zero", "config.yaml")
)


def main() -> int:
    from services.agent_zero.database import get_db_schema, get_engine, init_database

    init_database()
    print(f"数据库已初始化：engine={get_engine().url} schema={get_db_schema()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
