"""AtlasX 配置（本地单用户 / sqlite）。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

from services.shared.project_paths import project_data


class Settings(BaseSettings):
    """服务运行参数；数据库与 LLM 端点在 config.yaml 中配置。"""

    model_config = SettingsConfigDict(
        env_prefix="AGENT_ZERO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 服务
    host: str = "127.0.0.1"
    port: int = 10210
    debug: bool = False

    # 数据目录（相对项目根 data/ 下）
    data_dir: str = "agent_zero"
    workspace_dir: str = "agent_zero/workspace"

    log_level: str = "INFO"

    # 对话运行并发上限
    max_concurrent_conversation_runs: int = 4
    # 上下文压缩阈值百分比（旧口径；未配置时走 OpenCode compaction 语义：
    # 逼近窗口即压缩，详见 config.yaml llm.compaction）
    context_compress_percent: int = 70
    # 自主执行墙钟上限（秒）；0 = 不限（vibe coding 默认一直跑到任务完成）
    autonomous_run_max_seconds: int = 0

    def resolve_data_dir(self) -> Path:
        p = Path(self.data_dir)
        return p if p.is_absolute() else project_data(p.as_posix())

    def resolve_workspace_dir(self) -> Path:
        p = Path(self.workspace_dir)
        return p if p.is_absolute() else project_data(p.as_posix())


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
