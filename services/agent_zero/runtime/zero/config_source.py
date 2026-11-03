"""角色能力包络（对话执行体使用）。

本地单用户模式只保留一个默认角色，能力（工具 / 网络 / 可写路径）声明在
`roles.envelope`；模型与端点来自 config.yaml 的 `llm` 段。
"""

from __future__ import annotations

from typing import Any, Dict

# 网络策略：deny（禁网）/ allowlist（仅 egress_allowlist）/ allow（外网 + 内网）
NETWORK_LEVELS = ("deny", "allowlist", "allow")

DEFAULT_ENVELOPE = {
    "tools": [],
    "data_scope": "camp",
    "network": "allow",
    "egress_allowlist": [],
    "write_paths": ["workspace"],
}


def envelope_from_role(role: Dict[str, Any]) -> Dict[str, Any]:
    """角色 envelope → 归一化能力包络。"""
    raw = dict((role or {}).get("envelope") or {})
    network = str(raw.get("network") or DEFAULT_ENVELOPE["network"]).strip().lower()
    if network not in NETWORK_LEVELS:
        network = DEFAULT_ENVELOPE["network"]
    return {
        "tools": [str(x) for x in (raw.get("tools") or [])],
        "data_scope": str(raw.get("data_scope") or DEFAULT_ENVELOPE["data_scope"]),
        "network": network,
        "egress_allowlist": [str(x) for x in (raw.get("egress_allowlist") or [])],
        "write_paths": [str(x) for x in (raw.get("write_paths") or ["workspace"])],
    }
