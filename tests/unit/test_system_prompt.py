"""系统提示词：稳定准则与动态上下文的拼装。"""
from __future__ import annotations

from services.agent_zero.task_layer import prompt_builder as pb


def _ctx(**kw):
    base = dict(name="Atlas", now="2026-11-01 14:00:00")
    base.update(kw)
    return pb.PromptContext(**base)


def test_policy_sections_are_ordered_and_complete():
    text = pb.build(_ctx())
    for heading in ("# 身份与环境", "# 工作方式", "# 工具使用", "# 任务清单",
                    "# 子代理与后台任务", "# 文件与代码", "# 验证闭环",
                    "# 输出与沟通", "# 安全与权限"):
        assert heading in text
    order = ["# 工作方式", "# 工具使用", "# 任务清单", "# 子代理与后台任务",
             "# 文件与代码", "# 验证闭环", "# 输出与沟通", "# 安全与权限"]
    indexes = [text.index(h) for h in order]
    assert indexes == sorted(indexes)


def test_read_before_edit_and_verification_are_explicit():
    text = pb.build(_ctx())
    assert "先读后改" in text
    assert "修改任何文件前先读它" in text
    assert "验证闭环" in text
    assert "未验证" in text
    assert "不要 commit / push" in text
    assert "不要顺手重构" in text


def test_autonomous_uses_todo_list_and_drops_propose():
    text = pb.build(_ctx(autonomous=True))
    assert "# 自主执行模式（动态规划）" in text
    assert "todo_write" in text
    assert "# 何时转为自主执行" not in text

    normal = pb.build(_ctx(autonomous=False))
    assert "# 何时转为自主执行" in normal
    assert "# 自主执行模式（动态规划）" not in normal


def test_workspace_and_project_context_are_injected():
    text = pb.build(_ctx(
        workspace="工作区：Demo（id=ws，工作目录=/tmp/demo）",
        workspace_has_dir=True,
        rules="使用 pytest。",
        memories="- [convention] 提交信息用中文",
        skills="## 可用技能\n- **review**：审查改动",
        team="主持：Atlas",
        goal="目标：修复登录超时",
        progress="步骤 1/3 完成",
    ))
    assert "工作区：Demo" in text
    assert "使用 pytest。" in text
    assert "提交信息用中文" in text
    assert "可用技能" in text
    assert "主持：Atlas" in text
    assert "修复登录超时" in text
    assert "步骤 1/3 完成" in text


def test_empty_context_does_not_add_empty_headings():
    text = pb.build(_ctx())
    for heading in ("# 项目约定", "# 相关记忆", "# Agent 团队",
                    "# 目标模式", "# 当前工作区"):
        assert heading not in text


def test_chat_only_workspace_runs_in_managed_folder():
    """纯聊天工作区：不隐藏 shell / 文件工具，而是说明它们落在托管目录。"""
    text = pb.build(_ctx(workspace="工作区：默认（纯聊天工作区，无用户文件夹）",
                         workspace_has_dir=False))
    assert "纯聊天工作区" in text
    assert "托管目录" in text
    assert "`workspace` 工具" in text


def test_permission_section_follows_mode():
    assert "# 危险操作与权限" in pb.build(_ctx(permission_mode="once"))
    assert "# 危险操作与权限" not in pb.build(_ctx(permission_mode="always"))
    auto = pb.build(_ctx(permission_mode="once", auto_execute=True))
    assert "# 危险操作与权限" not in auto
    assert "# 自动执行模式（用户已开启，跳过确认）" in auto


def test_identity_uses_role_name_and_time():
    text = pb.build(_ctx(name="Reviewer", now="2026-11-02 09:30:00"))
    assert "你是「Reviewer」" in text
    assert "2026-11-02 09:30:00" in text
