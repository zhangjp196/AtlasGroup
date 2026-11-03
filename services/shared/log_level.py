"""从配置文件读取日志级别并应用到根日志器。"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_VALID = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_FALLBACK = "WARNING"


def normalize_level(value: object) -> str:
    s = str(value).strip().upper() if value else ""
    return s if s in _VALID else _FALLBACK


def apply_log_level_from_config(yaml_path: str) -> str:
    """从合并配置（common + 模块）读取 logging.log_level 并设置根日志器级别。

    返回实际生效的级别字符串。配置缺失或非法时使用 WARNING 兜底。
    """
    try:
        from services.shared.yaml_config import load_service_config

        cfg = load_service_config(yaml_path)
    except Exception as e:
        logger.warning("读取日志级别配置失败 %s: %s，使用 %s", yaml_path, e, _FALLBACK)
        logging.getLogger().setLevel(_FALLBACK)
        return _FALLBACK

    raw = (cfg.get("logging") or {}).get("log_level")
    level = normalize_level(raw)
    logging.getLogger().setLevel(level)
    logger.info("日志级别已设置为 %s（来源: %s）", level, yaml_path)
    return level
