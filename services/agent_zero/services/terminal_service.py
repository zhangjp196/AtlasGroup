"""在工作区目录打开宿主机的交互式终端。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def open_terminal(directory: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["/usr/bin/open", "-a", "Terminal", str(directory)],
                       check=True, capture_output=True, timeout=10)
    elif os.name == "nt":
        subprocess.Popen([os.environ.get("COMSPEC") or "cmd.exe"], cwd=directory,
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        for name in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
            executable = shutil.which(name)
            if executable:
                subprocess.Popen([executable], cwd=directory, start_new_session=True)
                return
        raise OSError("找不到已安装的终端程序")
