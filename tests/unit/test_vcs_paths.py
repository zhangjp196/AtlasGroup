from __future__ import annotations

import shutil
import subprocess

import pytest

from services.agent_zero.services import vcs_service


@pytest.mark.skipif(not shutil.which("git"), reason="git 未安装")
def test_git_status_keeps_chinese_paths_readable(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

    (repo / "项目分析.md").write_text("内容\n", encoding="utf-8")
    (repo / "PROJECT_ANALYSIS.md").write_text("x\n", encoding="utf-8")
    spaced = repo / "hz-3.0.0 bak"
    spaced.mkdir()
    (spaced / "a.sql").write_text("x\n", encoding="utf-8")
    entries = vcs_service._git_status_entries(repo)
    paths = {e["path"] for e in entries}
    assert "项目分析.md" in paths
    assert "PROJECT_ANALYSIS.md" in paths
    assert "hz-3.0.0 bak/" in paths, paths
    assert not any(p.startswith('"') for p in paths), paths
    assert not any("\\3" in p for p in paths), paths
