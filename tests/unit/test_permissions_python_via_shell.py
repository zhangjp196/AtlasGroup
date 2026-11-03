"""权限判定的回归测试：Python 代码从 shell 走之后，高危特征不能被漏掉。

背景（2026-10-30 整理时发现）：独立的 `python` 工具已经下线，模型要跑 Python
一律走 `shell`（`python3 -c '...'` / heredoc / `python x.py`）。但 `classify` 里
`PYTHON_PATTERNS`（`shutil.rmtree`、`os.remove`、`subprocess`、网络库…）只挂在
已不存在的 `python` 分支上——等于把这些风险标注悄悄关掉了：`python3 -c
"import shutil; shutil.rmtree(...)"` 只剩「不匹配 SHELL_PATTERNS」的裸风险。

这组测试钉住两点：
  1. shell 命令里**确实在跑 python** 时，PYTHON_PATTERNS 要参与判定；
  2. 只是普通命令提到这些字眼（`grep subprocess *.py`）不能被误判成高危理由。
"""
from __future__ import annotations

from services.agent_zero.task_layer import permissions


def _classify(cmd: str):
    return permissions.classify("shell", {"cmd": cmd})


def test_python_heredoc_removal_is_flagged():
    level, reasons = _classify("python3 - <<'PY'\nimport shutil\nshutil.rmtree('/tmp/x')\nPY")
    assert level == "high"
    assert any("python" in r for r in reasons), reasons


def test_python_inline_subprocess_is_flagged():
    _, reasons = _classify("python3 -c \"import subprocess; subprocess.run(['ls'])\"")
    assert any("执行外部命令" in r for r in reasons), reasons


def test_python_inline_network_is_flagged():
    _, reasons = _classify("python3 -c \"import requests; requests.get('http://x')\"")
    assert any("网络访问" in r for r in reasons), reasons


def test_plain_grep_for_python_keywords_is_not_flagged():
    """`grep subprocess *.py` 不是高危：没有在跑 python。"""
    _, reasons = _classify("grep -rn subprocess *.py")
    assert not any("python" in r for r in reasons), reasons


def test_running_tests_with_python_has_no_extra_reason():
    _, reasons = _classify("python3 -m pytest -q")
    assert not any("python" in r for r in reasons), reasons


def test_shell_patterns_still_apply():
    _, reasons = _classify("rm -rf /tmp/x")
    assert any("删除" in r for r in reasons), reasons


def test_plain_shell_is_medium_risk_so_auto_can_run_it():
    """Auto 档位只拦高危；普通 shell 命令必须是中危，否则 Auto 等于每次询问。"""
    level, reasons = _classify("ls -la")
    assert level == "medium", (level, reasons)
    assert not reasons, reasons


def test_python_tool_branch_is_gone():
    """独立 python 工具已下线：classify 不该再保留针对它的分支。

    保留的话，那段代码永远不会命中，却会让人以为「python 工具还在」。
    """
    import inspect

    src = inspect.getsource(permissions.classify)
    assert 'canon == "python"' not in src
    # 但特征库本身要留着（现在由 shell 分支复用）
    assert permissions.PYTHON_PATTERNS


def test_hook_config_write_is_forced_confirmation():
    """模型改 .atlas/hooks.json 必须人工确认（钩子会执行命令）。"""
    assert permissions._forced_confirmation(
        "write_file", {"path": ".atlas/hooks.json"}) is True
    assert permissions._forced_confirmation(
        "edit_file", {"path": "sub/.atlas/hooks.json"}) is True
    assert permissions._forced_confirmation(
        "shell", {"cmd": "echo '{}' > .atlas/hooks.json"}) is True
    # 普通文件写入不受影响
    assert permissions._forced_confirmation(
        "write_file", {"path": "src/app.py"}) is False
    # 只读查看钩子配置不算写
    assert permissions._forced_confirmation(
        "shell", {"cmd": "cat .atlas/hooks.json"}) is False
