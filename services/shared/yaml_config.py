"""YAML 配置加载：公共 config/common/config.yaml + 模块 config.yaml 深度合并（模块优先）。"""
from __future__ import annotations

import logging
import os
from typing import Any, Mapping

import yaml

logger = logging.getLogger(__name__)


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def common_config_path() -> str:
    return os.path.join(project_root(), "config", "common", "config.yaml")


def deep_merge_dict(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """递归合并字典；override 中的键覆盖 base。"""
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_yaml_file(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def load_merged_yaml(*yaml_paths: str) -> dict[str, Any]:
    """
    按顺序深度合并多个 YAML 文件，后者覆盖前者。
    默认首层为 config/common/config.yaml（若存在）。
    """
    merged: dict[str, Any] = {}
    common_path = common_config_path()
    paths: list[str] = []
    if os.path.isfile(common_path):
        paths.append(common_path)
    for path in yaml_paths:
        if not path:
            continue
        abs_path = os.path.abspath(path)
        if abs_path in {os.path.abspath(p) for p in paths}:
            continue
        paths.append(abs_path)

    for path in paths:
        if not os.path.isfile(path):
            logger.debug("配置文件不存在，跳过: %s", path)
            continue
        try:
            merged = deep_merge_dict(merged, load_yaml_file(path))
        except OSError as e:
            logger.warning("读取配置文件失败 %s: %s", path, e)
    return merged


def load_service_config(service_yaml_path: str, *override_yaml_paths: str) -> dict[str, Any]:
    """加载模块配置：公共配置 + 模块配置 + 可选覆盖层。"""
    return load_merged_yaml(service_yaml_path, *override_yaml_paths)
