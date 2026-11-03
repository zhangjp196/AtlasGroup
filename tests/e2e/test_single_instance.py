from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_second_instance_cannot_take_lock(tmp_path):
    from desktop import launcher

    home = tmp_path / "home"
    home.mkdir()
    holder = subprocess.Popen([
        sys.executable, "-c",
        "import sys,time; sys.path.insert(0, %r);"
        "from pathlib import Path;"
        "from desktop.launcher import _acquire_instance_lock;"
        "assert _acquire_instance_lock(Path(%r));"
        "print('locked', flush=True); time.sleep(5)"
        % (str(ROOT), str(home)),
    ], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        assert launcher._acquire_instance_lock(home) is False
    finally:
        holder.terminate()
        holder.wait(timeout=5)
    assert launcher._acquire_instance_lock(home) is True
