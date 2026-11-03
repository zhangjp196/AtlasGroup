"""Windows / POSIX shell 选择与输出解码。

Windows 支持不是文档里的一句话：`shell_argv` 会优先 PowerShell（5.x 的
`powershell` 或 7 的 `pwsh`），都没有才退回 cmd；两者都先把输出编码钉成 UTF-8，
否则中文 Windows 上命令输出全是乱码。
"""
from __future__ import annotations

from services.agent_zero.runtime.zero.sandbox import subprocess_sandbox as sb

PS = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
PWSH = r"C:\Program Files\PowerShell\7\pwsh.exe"
CMD = r"C:\Windows\System32\cmd.exe"


def test_posix_uses_bash(monkeypatch):
    monkeypatch.setattr(sb, "IS_WINDOWS", False)
    monkeypatch.setattr(sb.shutil, "which", lambda name: "/bin/bash" if name == "bash" else None)
    assert sb.shell_argv("ls -la") == ["/bin/bash", "-c", "ls -la"]


def test_windows_prefers_powershell(monkeypatch):
    monkeypatch.setattr(sb, "IS_WINDOWS", True)
    monkeypatch.setattr(sb.shutil, "which", lambda name: PS if name == "powershell" else None)
    argv = sb.shell_argv("Get-ChildItem")
    assert argv[0] == PS
    assert argv[1:4] == ["-NoProfile", "-NonInteractive", "-Command"]
    assert "UTF8" in argv[4]
    assert argv[4].endswith("Get-ChildItem")


def test_windows_falls_back_to_pwsh(monkeypatch):
    monkeypatch.setattr(sb, "IS_WINDOWS", True)
    monkeypatch.setattr(sb.shutil, "which", lambda name: PWSH if name == "pwsh" else None)
    argv = sb.shell_argv("dir")
    assert argv[0] == PWSH
    assert argv[1:4] == ["-NoProfile", "-NonInteractive", "-Command"]


def test_windows_falls_back_to_cmd(monkeypatch):
    monkeypatch.setattr(sb, "IS_WINDOWS", True)
    monkeypatch.setattr(sb.shutil, "which", lambda name: None)
    monkeypatch.setenv("COMSPEC", CMD)
    argv = sb.shell_argv("dir")
    assert argv[:4] == [CMD, "/d", "/s", "/c"]
    assert argv[4].startswith("chcp 65001 >nul & ")
    assert argv[4].endswith("dir")


def test_decode_output_utf8_then_codepage(monkeypatch):
    assert sb.decode_output("中文输出".encode("utf-8")) == "中文输出"
    monkeypatch.setattr(sb.locale, "getpreferredencoding",
                        lambda do_setlocale=True: "gbk")
    assert sb.decode_output("中文输出".encode("gbk")) == "中文输出"
    assert sb.decode_output(b"") == ""


def test_child_env_forces_utf8(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/should/be/dropped")
    env = sb._clean_env()
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"
    assert "PYTHONPATH" not in env
