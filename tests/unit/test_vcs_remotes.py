"""git 多远端管理 / 历史 refs 解析的回归测试。

这些能力直接服务于版本控制面板：远端列表、添加 / 修改 / 移除远端，
以及历史时间线需要的分支 / 标签装饰（`git log --decorate`）。
"""
from __future__ import annotations

import subprocess

import pytest

from services.agent_zero.services import vcs_service


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    monkeypatch.setattr(vcs_service, "_require_root",
                        lambda ws_id, vcs=None: tmp_path)
    return tmp_path


def test_git_remote_management(repo):
    vcs_service.remote_add("ws", "origin", "https://example.com/a.git")
    vcs_service.remote_add("ws", "backup", "git@example.com:org/b.git")

    data = vcs_service.remotes("ws")
    names = {r["name"] for r in data["remotes"]}
    assert names == {"origin", "backup"}
    origin = next(r for r in data["remotes"] if r["name"] == "origin")
    assert origin["fetch_url"] == "https://example.com/a.git"

    vcs_service.remote_set_url("ws", "origin", "https://example.com/c.git")
    origin = next(r for r in vcs_service.remotes("ws")["remotes"]
                  if r["name"] == "origin")
    assert origin["fetch_url"] == "https://example.com/c.git"

    vcs_service.remote_remove("ws", "backup")
    assert [r["name"] for r in vcs_service.remotes("ws")["remotes"]] == ["origin"]


def test_remote_name_and_url_are_validated(repo):
    with pytest.raises(vcs_service.VcsError):
        vcs_service.remote_add("ws", "bad name", "https://example.com/a.git")
    with pytest.raises(vcs_service.VcsError):
        vcs_service.remote_add("ws", "origin", "--upload-pack=evil")
    with pytest.raises(vcs_service.VcsError):
        vcs_service.remote_add("ws", "origin", "ext::sh -c evil")


def test_git_log_exposes_refs_for_history_timeline(repo):
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "first commit")
    _git(repo, "tag", "v1.0.0")

    commits = vcs_service._git_log(repo, 5)
    assert commits
    assert commits[0]["message"] == "first commit"
    assert any(ref.startswith("tag: v1.0.0") or ref == "v1.0.0"
               for ref in commits[0]["refs"])
