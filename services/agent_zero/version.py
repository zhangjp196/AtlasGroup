"""品牌与版本号的唯一来源。

- `APP_NAME`：产品显示名（窗口 / 菜单 / 日志 / 提示词 / 文档统一引用它）；
- `APP_VERSION`：版本号唯一来源是仓库根目录的 `VERSION` 文件（打包后放在
  `sys._MEIPASS` 根目录；源码运行时读仓库根）。

**标识层保持 Atlas 旧名**（数据目录 / 锁名 / bundle id / Keychain service /
`.atlas` 约定 / `agent_zero` 模块路径）：改名会让已有会话、附件与已存的 API Key
失联，属于独立的迁移决策，不在显示层改名里做。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 产品显示名。所有用户可见的品牌文案都引用它（见 README「命名与标识」）。
APP_NAME = "AtlasX"
# 旧品牌名：仅用于识别「还是默认名」的历史数据（如默认角色名迁移）。
LEGACY_APP_NAME = "Atlas"


def read_version() -> str:
    roots = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    roots.append(Path(__file__).resolve().parents[2])
    for root in roots:
        try:
            text = (root / "VERSION").read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            continue
    return "0.0.0"


APP_VERSION = read_version()
__version__ = APP_VERSION
