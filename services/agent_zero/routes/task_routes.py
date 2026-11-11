"""对话 / 运行 API（本地单用户）。

对话 = 唯一入口：发消息创建后台 run，由服务端 worker 执行；
关浏览器继续跑，重连按 seq 游标续拉。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from services.agent_zero.stores.zero_store import (
    DEFAULT_ROLE_ID,
    DEFAULT_WORKSPACE_ID,
    GLOBAL_ROLE_ID,
    get_workspace,
)
from services.agent_zero.stores import zero_store
from services.shared.auth import LOCAL_USER_ID
from services.agent_zero.task_layer import conversation, conv_worker, run_service

logger = logging.getLogger(__name__)

router = APIRouter()
USER = LOCAL_USER_ID


class CreateConvBody(BaseModel):
    workspace_id: Optional[str] = None
    role_id: Optional[str] = None
    zone: Optional[str] = None
    team_id: Optional[str] = None
    bound_workspace_id: Optional[str] = None
    model_code: Optional[str] = None
    thinking_level: Optional[str] = None
    permission_mode: Optional[str] = None
    run_mode: Optional[str] = None


@router.post("/conversations")
def create_conversation(body: CreateConvBody):
    zone = str(body.zone or "").strip() or None
    # AtlasX 只服务全局 Agent；普通会话默认落到「通用助手」。
    if zone == conversation.GLOBAL_AGENT_ZONE:
        role_id = body.role_id or GLOBAL_ROLE_ID
    else:
        role_id = body.role_id or DEFAULT_ROLE_ID
    # 工作区由角色 / 群组绑定决定；显式 workspace_id 优先（会话 / 后台任务等入口），
    # 并标记为本会话手动指定，之后切换角色 / 群组不覆盖。
    ws_id = (str(body.workspace_id or "").strip()
             or conversation.workspace_for_conversation(role_id, body.team_id)
             or DEFAULT_WORKSPACE_ID)
    if not get_workspace(ws_id):
        raise HTTPException(status_code=404, detail=f"工作区不存在: {ws_id}")
    try:
        conv = conversation.create_conversation(
            ws_id, USER, role_id=role_id, zone=zone,
            team_id=body.team_id,
            bound_workspace_id=body.bound_workspace_id,
            model_code=body.model_code, thinking_level=body.thinking_level,
            permission_mode=body.permission_mode, run_mode=body.run_mode,
            workspace_custom=bool(str(body.workspace_id or "").strip()
                                  or str(body.bound_workspace_id or "").strip()),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": conv}


class ConversationTitleBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class ConversationParticipantsBody(BaseModel):
    role_ids: List[str] = Field(default_factory=list, max_length=12)
    leader_id: Optional[str] = Field(None, max_length=120)


@router.put("/conversations/{conv_id}/participants")
def set_conversation_participants(conv_id: str, body: ConversationParticipantsBody):
    """Set conversation-local participants without modifying a shared legacy team."""
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.services import team_service

    conv = conversation.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    if conv.get("zone") == conversation.GLOBAL_AGENT_ZONE:
        raise HTTPException(status_code=400, detail="管理会话不能添加参与角色")
    old_team = team_service.get_team(str(conv.get("team_id") or "")) or {}
    leader_id = (str(body.leader_id or "").strip()
                 or str((old_team.get("leader") or {}).get("id") or "")
                 or str(conv.get("role_id") or DEFAULT_ROLE_ID))
    leader = zero_store.get_role(leader_id) or zero_store.get_role(DEFAULT_ROLE_ID)
    if not leader:
        raise HTTPException(status_code=400, detail="会话主持角色不存在")
    if leader_id == GLOBAL_ROLE_ID and conv.get("zone") != conversation.GLOBAL_AGENT_ZONE:
        raise HTTPException(status_code=400, detail="AtlasX 全局角色只能用于全局 Agent")
    if str(leader.get("scope") or "") == "temporary" \
            and str(leader.get("session_id") or "") != conv_id:
        raise HTTPException(status_code=400, detail="临时角色不属于当前会话")
    role_ids = list(dict.fromkeys(str(r).strip() for r in body.role_ids if str(r).strip()))
    members = []
    for role_id in role_ids:
        role = zero_store.get_role(role_id)
        if not role or role_id == GLOBAL_ROLE_ID:
            raise HTTPException(status_code=400, detail=f"角色不存在: {role_id}")
        if role.get("scope") == "temporary" and role.get("session_id") != conv_id:
            raise HTTPException(status_code=400, detail="不能使用其他会话的临时角色")
        if role_id != str(leader.get("id")):
            members.append({"id": role_id})

    # 主持角色可以随参与角色一起调整：直接写回会话，私有参与团队同步 leader。
    if str(conv.get("role_id") or "") != str(leader.get("id") or ""):
        SessionLocal = get_session_local()
        with SessionLocal() as db:
            db.execute(
                text("UPDATE conversations SET role_id = :rid, "
                     "updated_at = datetime('now','localtime') WHERE id = :id"),
                {"rid": str(leader.get("id")), "id": conv_id},
            )
            db.commit()

    workspace_id = (str(conv.get("workspace_id") or "")
                    or str(leader.get("group_id") or "")
                    or DEFAULT_WORKSPACE_ID)
    current_team_id = str(conv.get("team_id") or "")
    private_prefix = f"participants-{conv_id}-"
    existing = (old_team if current_team_id.startswith(private_prefix)
                and team_service.bound_conversation_ids(current_team_id) == [conv_id] else None)
    team_id = current_team_id if existing else f"{private_prefix}{zero_store.new_id('set')}"
    if existing:
        team = team_service.update_team(
            team_id, leader={"id": leader["id"]}, members=members,
            workspace_id=workspace_id)
    else:
        team = team_service.create_team(
            workspace_id,
            str(conv.get("title") or "会话参与角色"),
            leader={"id": leader["id"]}, members=members,
            mode=str(old_team.get("mode") or "orchestrate"), team_id=team_id,
        )
    if current_team_id != team_id:
        team_service.bind_team(conv_id, team_id)
    conversation.sync_conversation_workspace(conv_id)
    return {"code": 0, "data": {"conversation": conversation.get_conversation(conv_id), "team": team}}


@router.patch("/conversations/{conv_id}")
def update_conversation_title(conv_id: str, body: ConversationTitleBody):
    """手动重命名会话（title_source=user，后续 AI 生成不再覆盖）。"""
    from services.agent_zero.task_layer import title_service

    if not conversation.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    data = title_service.rename(conv_id, body.title)
    if not data:
        raise HTTPException(status_code=400, detail="标题为空或对话不存在")
    return {"code": 0, "data": data}


class ConversationSettingsBody(BaseModel):
    """会话管理：标题 / 单聊角色 / 群组 / 工作区。

    未出现在请求里的字段保持不变；显式传空字符串表示清空（如 team_id="" 退出群聊）。
    """

    title: Optional[str] = Field(None, max_length=200)
    role_id: Optional[str] = Field(None, max_length=80)
    team_id: Optional[str] = Field(None, max_length=80)
    # 手动指定本会话工作区；传空字符串表示回到角色 / 群组默认工作区。
    workspace_id: Optional[str] = Field(None, max_length=80)
    # true = 清除手动指定，重新跟随角色 / 群组（与 workspace_id 同时出现时优先）。
    workspace_follow: Optional[bool] = None
    # 旧字段：等价于手动指定 workspace_id。
    bound_workspace_id: Optional[str] = Field(None, max_length=80)


@router.patch("/conversations/{conv_id}/settings")
def update_conversation_settings(conv_id: str, body: ConversationSettingsBody):
    """会话管理入口：一次保存角色 / 群组 / 工作区 / 标题。"""
    from sqlalchemy import text

    from services.agent_zero.database import get_session_local
    from services.agent_zero.services import team_service
    from services.agent_zero.task_layer import title_service

    conv = conversation.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    fields = body.model_fields_set
    zone = str(conv.get("zone") or "")
    SessionLocal = get_session_local()

    def _set_role(role_id: Optional[str]) -> None:
        with SessionLocal() as db:
            db.execute(
                text("UPDATE conversations SET role_id = :rid, "
                     "updated_at = datetime('now','localtime') WHERE id = :id"),
                {"rid": role_id or None, "id": conv_id},
            )
            db.commit()

    if "title" in fields:
        title = str(body.title or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="标题不能为空")
        title_service.rename(conv_id, title)

    if "role_id" in fields:
        role_id = str(body.role_id or "").strip()
        if role_id:
            role = zero_store.get_role(role_id)
            if not role:
                raise HTTPException(status_code=400, detail=f"角色不存在: {role_id}")
            if role_id == GLOBAL_ROLE_ID and zone != conversation.GLOBAL_AGENT_ZONE:
                raise HTTPException(status_code=400,
                                    detail="AtlasX 全局角色只能用于全局 Agent")
            if str(role.get("scope") or "") == "temporary" \
                    and str(role.get("session_id") or "") != conv_id:
                raise HTTPException(status_code=400, detail="临时角色不属于当前会话")
        _set_role(role_id or None)

    if "team_id" in fields:
        team_id = str(body.team_id or "").strip()
        current_team = str(conv.get("team_id") or "").strip()
        if team_id:
            team = team_service.get_team(team_id)
            if not team:
                raise HTTPException(status_code=400, detail=f"群组不存在: {team_id}")
            if not (team.get("members") or []):
                raise HTTPException(status_code=400,
                                    detail="这个群组还没有成员，先添加成员再绑定")
            if team_id != current_team:
                try:
                    team_service.bind_team(conv_id, team_id)
                except ValueError as e:
                    raise HTTPException(status_code=400, detail=str(e))
            if "role_id" not in fields:
                leader_id = str((team.get("leader") or {}).get("id") or "")
                if leader_id:
                    _set_role(leader_id)
        elif current_team:
            team_service.bind_team(conv_id, None, notice="你已退出群聊")

    role_changed = ("role_id" in fields
                    and str(body.role_id or "").strip() != str(conv.get("role_id") or ""))
    team_changed = ("team_id" in fields
                    and str(body.team_id or "").strip() != str(conv.get("team_id") or ""))
    if role_changed or team_changed:
        conversation.sync_conversation_workspace(conv_id)

    # 工作区最后处理：同一次保存里既切角色 / 群组又选工作区时，显式选择优先。
    follow = ("workspace_follow" in fields and bool(body.workspace_follow))
    if follow:
        conversation.sync_conversation_workspace(conv_id, force=True)
    elif "workspace_id" in fields:
        try:
            conversation.set_conversation_workspace(conv_id, body.workspace_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    elif "bound_workspace_id" in fields:
        try:
            conversation.update_bound_workspace(conv_id, body.bound_workspace_id or None)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    updated = conversation.get_conversation(conv_id) or conv
    updated["created_at"] = str(updated.get("created_at") or "")
    updated["updated_at"] = str(updated.get("updated_at") or "")
    return {"code": 0, "data": updated}


@router.post("/conversations/{conv_id}/title/regenerate")
async def regenerate_conversation_title(conv_id: str):
    """用轻量模型重新生成标题（可覆盖用户标题，属于用户主动操作）。"""
    from services.agent_zero.task_layer import title_service

    if not conversation.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    title = await title_service.generate(conv_id, force=True)
    data = title_service.get_title(conv_id)
    data["generated"] = bool(title)
    return {"code": 0, "data": data}


class MessageAttachmentBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    mime: Optional[str] = Field("", max_length=200)
    size: Optional[int] = Field(0, ge=0)
    # base64（可带 data:...;base64, 前缀）；单个附件由服务端限制 25 MB。
    data: str = Field(..., min_length=1, max_length=80_000_000)


class MessageBody(BaseModel):
    # 允许空正文：只发图片 / 附件也是合法消息（附件负责提供内容）。
    content: str = Field("", max_length=2_000_000)
    refs: Optional[List[str]] = None
    auto_execute: Optional[bool] = None
    mode: Optional[str] = Field(None, pattern="^(chat|autonomous|goal)$")
    # 插队：兼容旧客户端（新前端改为队列条上的上下移动自由排序）
    priority: Optional[bool] = False
    attachments: Optional[List[MessageAttachmentBody]] = None
    # 思考级别（reasoning effort）；仅当当前模型配置了 thinking_levels 时生效
    thinking_level: Optional[str] = Field(None, max_length=40)
    # 圆桌 / 群聊点名：成员 id 或名字（前端从 @xxx 里解析；后端也兼容内容里的 @）
    mentions: Optional[List[str]] = Field(None, max_length=16)


@router.post("/conversations/{conv_id}/messages/run")
async def conv_message_run(conv_id: str, body: MessageBody):
    """发消息并入队执行（队列模式：运行中也可继续发，按顺序排队）。

    mode=goal：确保对话有一个进行中的目标（首次发送时用本条消息作为目标），
    run 本身按自主执行跑；run 结束后由 worker 自动续跑，直到目标达成 / 预算用尽。
    """
    conv = conversation.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    if not str(body.content or "").strip() and not body.attachments:
        raise HTTPException(status_code=400, detail="消息为空")
    mode = body.mode or conv.get("run_mode") or "autonomous"
    if mode not in ("chat", "autonomous", "goal"):
        mode = "autonomous"
    goal_started = False
    if mode == "goal":
        from services.agent_zero.task_layer import goal_service

        try:
            if not goal_service.active_goal(conv_id):
                goal_service.set_goal(conv_id, body.content)
                goal_started = True
            conversation.set_run_mode(conv_id, "goal")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    run_mode = "autonomous" if mode == "goal" else mode
    from services.agent_zero.runtime.zero import llm as llm_mod

    conv_model = str(conv.get("model_code") or "").strip() \
        or llm_mod.get_active_model_code()
    allowed_levels = llm_mod.get_thinking_levels_for(conv_model)
    raw_level = body.thinking_level
    if raw_level is None:
        thinking_level = str(conv.get("thinking_level") or "").strip()
    else:
        thinking_level = str(raw_level or "").strip()
    if allowed_levels:
        if thinking_level and thinking_level not in allowed_levels:
            raise HTTPException(status_code=400,
                                detail=f"思考级别不可用：{thinking_level}")
    else:
        thinking_level = ""
    try:
        conversation.update_conversation_preferences(
            conv_id, thinking_level=thinking_level)
    except Exception:
        pass
    attachments = None
    if body.attachments:
        from services.agent_zero.services import attachment_service

        try:
            attachments = attachment_service.process_uploads(
                conv_id,
                [a.model_dump() for a in body.attachments],
                allow_images=conversation.chat_vision_for(conv),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    msg: Optional[dict] = None
    # 斜杠命令：转录里保留 /review 这样的原文，模型侧用展开后的完整指令。
    command_prompt = ""
    try:
        from services.agent_zero.services import command_service

        _cmd = await asyncio.to_thread(
            command_service.resolve, conv.get("workspace_id"), body.content)
        if _cmd:
            command_prompt = str(_cmd.get("prompt") or "")
    except Exception as _ce:
        logger.warning("斜杠命令展开失败（按普通消息发送）: %s", _ce)
    try:
        msg = conversation.send_message(conv_id, body.content, USER,
                                        refs=body.refs, queued=True,
                                        attachments=attachments,
                                        prompt=command_prompt)
        run = run_service.create_run(
            conv_id, USER, role_id=conv.get("role_id"),
            content=body.content, mode=run_mode,
            auto_execute=bool(body.auto_execute),
            queued_msg_id=msg.get("id"),
            thinking_level=thinking_level,
            mentions=[str(x)[:64] for x in (body.mentions or []) if str(x).strip()][:16]
            or None,
        )
        # 旧客户端的「插队」入参：保留能力（新前端已改用队列条上的上下移动），
        # 建完 run 直接挪到队首。
        if body.priority:
            run_service.prioritize_run(run["id"])
            run = run_service.get_run(run["id"]) or run
    except ValueError as e:
        if attachments:
            from services.agent_zero.services import attachment_service

            attachment_service.remove_attachments(conv_id, attachments)
        if msg and msg.get("id"):
            conversation.delete_message(msg["id"])
        raise HTTPException(status_code=400, detail=str(e))
    try:
        await conv_worker.request_run(run["id"])
    except Exception as e:
        logger.warning("对话运行触发失败（worker 周期会拾取）: %s", e)
    queue = run_service.conversation_queue(conv_id)
    position = next((i + 1 for i, r in enumerate(queue) if r["id"] == run["id"]), None)
    running = run_service.running_run(conv_id, include_member=False)
    data = dict(run)
    data["intervened"] = False
    data["queued"] = True
    data["queue_position"] = position
    data["goal_started"] = goal_started
    data["queue"] = queue
    data["running_run_id"] = (running or {}).get("id")
    return {"code": 0, "data": data}


@router.post("/conversations/{conv_id}/compact")
async def compact_conversation(conv_id: str):
    """手动压缩上下文：把旧历史压成检查点，后续轮次继续使用。

    没有可折叠历史时只返回原因，不报错；运行中的会话要求先停止，避免
    压缩和正在执行的 run 同时改同一份上下文。
    """
    conv = conversation.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    if run_service.running_run(conv_id):
        raise HTTPException(status_code=409, detail="当前会话正在运行，先停止再压缩")
    try:
        role = zero_store.get_role(str(conv.get("role_id") or "")) or {}
        models = conversation._chat_models(role) or {"loop": "default", "fallback": "default"}
        messages = conversation._history_messages(conv_id)
        from services.agent_zero.runtime.zero import compress

        decision = await compress.decide(
            messages, models=models, force=True, reason="manual")
        if not decision.get("should_compress"):
            return {"code": 0, "data": {
                "compressed": False,
                "reason": decision.get("reason") or "nothing",
                "before_tokens": int(decision.get("current") or 0),
                "after_tokens": int(decision.get("current") or 0),
            }}
        result = await compress.apply(
            messages, models=models, decision=decision, force_summary=True)
        if result.get("summary"):
            conversation.persist_checkpoint(
                conv, str(result.get("summary") or ""),
                str(result.get("source") or "manual"))
        return {"code": 0, "data": {
            "compressed": bool(result.get("compressed")),
            "source": result.get("source") or "",
            "before_tokens": int(result.get("original_tokens") or 0),
            "after_tokens": int(result.get("tokens") or 0),
            "pruned_tool_outputs": int(result.get("pruned_tool_outputs") or 0),
            "dropped_exchanges": int(result.get("dropped_exchanges") or 0),
        }}
    except Exception as e:
        logger.warning("手动压缩失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"压缩失败: {e}")


class ClearContextBody(BaseModel):
    # all = 清空所有会话的模型上下文；current = 只清当前会话。
    scope: Optional[str] = Field("all", pattern="^(all|current)$")


@router.post("/conversations/{conv_id}/clear")
def clear_conversation_context(conv_id: str, body: Optional[ClearContextBody] = None):
    """清空模型上下文，但**不删除**历史消息（转录仍可回看）。"""
    conv = conversation.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    scope = (body.scope if body else "all") or "all"
    if scope == "current":
        ok = conversation.clear_context(conv_id)
        return {"code": 0, "data": {"cleared": 1 if ok else 0, "scope": scope}}
    count = conversation.clear_all_contexts()
    return {"code": 0, "data": {"cleared": count, "scope": "all"}}


@router.post("/conversations/{conv_id}/clean")
def clean_conversation(conv_id: str):
    """彻底清空会话：上下文 + 历史消息 + 运行 / 后台任务 / 审批痕迹。

    ``/clear`` 只让模型看不到旧上下文（转录可回看）；``/clean`` 连历史一起删，
    会话本身（标题 / 群组 / 工作区）保留。前端负责二次确认。
    """
    if not conversation.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    try:
        return {"code": 0, "data": conversation.clean_conversation(conv_id)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class MessageEditBody(BaseModel):
    content: str = Field(..., min_length=1)


@router.patch("/conversations/{conv_id}/messages/{msg_id}")
async def edit_conversation_message(conv_id: str, msg_id: str,
                                    body: MessageEditBody):
    """编辑用户消息。

    排队中：替换排队 run（停止原排队项，保留位置/优先级）；
    已执行：截断该消息之后的对话，用新正文重新发起一条 run。
    """
    try:
        command_prompt = ""
        conv = conversation.get_conversation(conv_id)
        try:
            from services.agent_zero.services import command_service

            _cmd = await asyncio.to_thread(
                command_service.resolve, (conv or {}).get("workspace_id"), body.content)
            if _cmd:
                command_prompt = str(_cmd.get("prompt") or "")
        except Exception as _ce:
            logger.warning("斜杠命令展开失败（按普通消息处理）: %s", _ce)
        result = conversation.edit_message(conv_id, msg_id, body.content,
                                           prompt=command_prompt)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    run = result.get("run") or {}
    if run.get("id"):
        try:
            await conv_worker.request_run(run["id"])
        except Exception:
            pass
    return {"code": 0, "data": result}


@router.post("/conversations/{conv_id}/messages/{msg_id}/recall")
def recall_conversation_message(conv_id: str, msg_id: str):
    """撤回用户消息：排队项直接删除；已执行的截断该消息及其后的对话。"""
    try:
        result = conversation.recall_message(conv_id, msg_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": result}


# ── 会话分支（fork）───────────────────────────────────────────────────────
# 从某条消息处分叉出一个新会话：复制历史消息与附件，不改动工作区文件。


@router.post("/conversations/{conv_id}/fork")
def fork_conversation(conv_id: str, body: ForkBody):
    """从 message_id（含）处复制出一个新会话；省略 message_id 复制整段。"""
    _conv_or_404(conv_id)
    try:
        data = conversation.fork_conversation(
            conv_id, message_id=(body.message_id or "").strip() or None,
            user=USER, title=body.title)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": data}


# ── 文件检查点 / 回退（rewind）────────────────────────────────────────────
# 每个用户消息的 run 真正执行前自动拍一次工作区快照；回退可只恢复文件、
# 只截断对话，或两者都做。


class RewindBody(BaseModel):
    files: bool = True
    conversation: bool = True
    delete_added: bool = True


class CheckpointBody(BaseModel):
    message_id: str = ""
    label: str = ""


class ForkBody(BaseModel):
    message_id: Optional[str] = None
    title: Optional[str] = None


def _checkpoint_service():
    from services.agent_zero.services import file_checkpoint_service

    return file_checkpoint_service


def _conv_or_404(conv_id: str) -> dict:
    conv = conversation.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    return conv


def _owned_checkpoint(conv: dict, checkpoint_id: str) -> dict:
    svc = _checkpoint_service()
    try:
        cp = svc.get(str(conv.get("workspace_id") or ""), checkpoint_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if str(cp.get("conversation_id") or "") != str(conv.get("id") or ""):
        raise HTTPException(status_code=404, detail=f"检查点不存在: {checkpoint_id}")
    return cp


@router.get("/conversations/{conv_id}/checkpoints")
def list_file_checkpoints(conv_id: str):
    """该会话的文件检查点（按创建时间倒序，最新在前）。"""
    conv = _conv_or_404(conv_id)
    svc = _checkpoint_service()
    try:
        items = svc.list_for_conversation(
            conv_id, str(conv.get("workspace_id") or ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": {"items": items}}


@router.post("/conversations/{conv_id}/checkpoints")
def create_file_checkpoint(conv_id: str, body: CheckpointBody):
    """手动给当前工作区拍一次检查点（不依赖 run）。"""
    conv = _conv_or_404(conv_id)
    from services.agent_zero.services import workspace_service

    ws_id = str(conv.get("workspace_id") or "")
    if not workspace_service.has_working_dir(ws_id):
        raise HTTPException(status_code=400,
                            detail="该会话没有工作目录，无法创建文件检查点")
    svc = _checkpoint_service()
    try:
        cp = svc.snapshot(ws_id, conversation_id=conv_id,
                          message_id=str(body.message_id or "").strip(),
                          label=str(body.label or "").strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": cp}


@router.get("/conversations/{conv_id}/checkpoints/{checkpoint_id}/diff")
def diff_file_checkpoint(conv_id: str, checkpoint_id: str):
    """回退前预览：相对当前工作区的新增 / 修改 / 删除文件。"""
    conv = _conv_or_404(conv_id)
    _owned_checkpoint(conv, checkpoint_id)
    svc = _checkpoint_service()
    try:
        data = svc.diff(str(conv.get("workspace_id") or ""), checkpoint_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": data}


@router.post("/conversations/{conv_id}/checkpoints/{checkpoint_id}/rewind")
async def rewind_file_checkpoint(conv_id: str, checkpoint_id: str,
                                 body: RewindBody):
    """回退到检查点：恢复文件 + / 或删除该消息及其后的对话。

    有运行正在执行时拒绝（先停止）；只回退文件时不动对话。
    """
    conv = _conv_or_404(conv_id)
    cp = _owned_checkpoint(conv, checkpoint_id)
    if not body.files and not body.conversation:
        raise HTTPException(status_code=400, detail="至少选择回退文件或对话")
    if run_service.running_run(conv_id):
        raise HTTPException(status_code=400,
                            detail="当前有运行正在执行，请先停止后再回退")

    ws_id = str(conv.get("workspace_id") or "")
    message_id = str(cp.get("message_id") or "")
    files_result = None
    conv_result = None

    # 先校验对话侧（检查点可能已被别处清理），避免文件恢复了才发现消息不在了。
    if body.conversation:
        if not message_id:
            raise HTTPException(status_code=400, detail="该检查点没有关联消息")
        if not conversation.get_message_node(conv_id, message_id):
            raise HTTPException(status_code=400,
                                detail="这条消息已被删除，无法只回退对话")

    # 先恢复文件：对话截断会连带清理检查点（包括这一条）。
    if body.files:
        from services.agent_zero.services import workspace_service

        if not workspace_service.has_working_dir(ws_id):
            raise HTTPException(status_code=400,
                                detail="该会话没有工作目录，无法回退文件")
        svc = _checkpoint_service()
        try:
            files_result = await asyncio.to_thread(
                svc.restore, ws_id, checkpoint_id,
                delete_added=bool(body.delete_added))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"回退文件失败: {e}")

    if body.conversation:
        run_service.cancel_queued_runs(conv_id)
        removed = conversation.truncate_messages(conv_id, message_id,
                                                 include_self=True)
        conv_result = {"messages_removed": removed, "message_id": message_id}

    zero_store.append_audit(
        "local", "checkpoint_rewind",
        args={"conversation_id": conv_id, "checkpoint_id": checkpoint_id,
              "files": bool(body.files), "conversation": bool(body.conversation),
              "delete_added": bool(body.delete_added)},
        result={"files": files_result, "conversation": conv_result},
    )
    return {"code": 0, "data": {"files": files_result,
                                "conversation": conv_result}}


@router.delete("/conversations/{conv_id}/checkpoints/{checkpoint_id}")
def delete_file_checkpoint(conv_id: str, checkpoint_id: str):
    """删除一条检查点（不影响工作区文件与对话）。"""
    conv = _conv_or_404(conv_id)
    _owned_checkpoint(conv, checkpoint_id)
    svc = _checkpoint_service()
    deleted = svc.delete(str(conv.get("workspace_id") or ""), checkpoint_id)
    return {"code": 0, "data": {"deleted": deleted}}


@router.get("/conversations/{conv_id}/queue")
def get_conversation_queue(conv_id: str):
    """队列模式：该对话的排队 run（按 queue_pos 升序）+ 当前正在执行的 run。"""
    if not conversation.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    return {"code": 0, "data": {
        "items": run_service.conversation_queue(conv_id),
        # 主会话只认主持 run：成员的后台并行 run 在执行画布里看，不能把
        # 主会话的发送按钮锁住（用户要求：后台任务靠触发器推进，不是一直等）。
        "running": run_service.running_run(conv_id, include_member=False),
    }}


def _queued_run_or_404(conv_id: str, run_id: str) -> dict:
    run = run_service.get_run(run_id)
    if (not run or run.get("conversation_id") != conv_id
            or run.get("status") != run_service.STATUS_QUEUED):
        raise HTTPException(status_code=404, detail=f"排队消息不存在: {run_id}")
    return run


class MoveQueuedBody(BaseModel):
    # 'up' = 提前一位，'down' = 推后一位（队列条上的上下移动按钮）
    direction: str = Field(..., pattern="^(up|down)$")


@router.post("/conversations/{conv_id}/queue/{run_id}/move")
def move_queued_run(conv_id: str, run_id: str, body: MoveQueuedBody):
    """上移 / 下移一条排队消息（自由调整执行顺序）。

    只在 queued 内调整，不影响正在执行的 run。已是队首再上移 / 队尾再下移
    返回 moved=False（前端据此提示而不是报错）。
    """
    _queued_run_or_404(conv_id, run_id)
    moved = run_service.move_queued_run(run_id, body.direction)
    return {"code": 0, "data": {"moved": moved,
                                "direction": body.direction,
                                "items": run_service.conversation_queue(conv_id)}}


@router.post("/conversations/{conv_id}/queue/{run_id}/prioritize")
async def prioritize_queued_run(conv_id: str, run_id: str):
    """（兼容旧客户端）把排队消息移到队首。

    新前端不再用它：队列条改为「上移 / 下移」自由排序，一次只挪一位，
    语义更可预期。保留此端点是为了不打断仍在调用它的旧页面。
    """
    _queued_run_or_404(conv_id, run_id)
    if not run_service.prioritize_run(run_id):
        raise HTTPException(status_code=409, detail="移动到队首失败（状态已变化）")
    try:
        await conv_worker.request_run(run_id)
    except Exception:
        pass
    return {"code": 0, "data": {"items": run_service.conversation_queue(conv_id)}}


@router.post("/conversations/{conv_id}/queue/{run_id}/inject")
async def inject_queued_run(conv_id: str, run_id: str):
    """立即插入 = 停止当前回复，然后把这条排队消息当下一轮执行。

    旧实现把消息交给执行中的 run 当「干预」注入：正文仍在原位继续输出，而消息
    被取走后又从队列里消失，界面就出现「输出位置没变、消息却在回答上面/下面跳」
    的错乱（用户反馈：「立即插入 需要调整为 停止然后继续，目前跟队列一样，但输出
    又在原来的位置，导致界面异常」）。

    新语义不再碰正在生成的这轮：先把它停下（与点「停止」同一条路径，含强制取消
    与兜底收尾），再让它在队首等 worker 认领——用户看到的就是「停 → 接着跑新消息」。
    """
    _queued_run_or_404(conv_id, run_id)
    running = run_service.running_run(conv_id, include_member=False)
    if not running:
        raise HTTPException(status_code=409,
                            detail="当前没有正在执行的运行，无法立即插入（可直接等待执行）")
    if not run_service.inject_run(run_id):
        raise HTTPException(status_code=409, detail="插入失败（状态已变化）")

    from services.agent_zero.task_layer.conv_worker import get_worker

    stopped_run_id = str(running.get("id") or "")
    # 静默停止：这一轮是被「接着跑新消息」正常接续的，用户不该看到
    # 「运行已停止」提示与「本轮输出已中断」红条（用户要求：停止然后继续是静默的）。
    run_service.request_stop(stopped_run_id, silent=True)
    forced = get_worker().force_stop(stopped_run_id)
    if not forced:
        # 没有存活任务（执行任务已不在本进程 / 卡在不可取消的同步段）：立即收尾，
        # 否则这条 running 会一直占着该对话，队首的插入消息永远等不到调度。
        get_worker()._force_finalize(stopped_run_id)
    return {"code": 0, "data": {
        "items": run_service.conversation_queue(conv_id),
        "stopped_run_id": stopped_run_id,
        "forced": bool(forced),
    }}


@router.delete("/conversations/{conv_id}/queue/{run_id}")
def cancel_queued_run(conv_id: str, run_id: str):
    """取消一条排队消息（同时从转录中移除）。"""
    _queued_run_or_404(conv_id, run_id)
    if not run_service.cancel_run(run_id):
        raise HTTPException(status_code=409, detail="取消失败（状态已变化）")
    return {"code": 0, "data": {"items": run_service.conversation_queue(conv_id)}}


@router.get("/conversations")
def list_conversations(workspace_id: Optional[str] = None,
                       keyword: Optional[str] = None,
                       limit: int = 50, offset: int = 0,
                       include_archived: bool = False):
    items = conversation.list_conversations(
        workspace_id=workspace_id, keyword=keyword, limit=limit, offset=offset,
        include_archived=include_archived)
    total = conversation.count_conversations(
        workspace_id=workspace_id, keyword=keyword,
        include_archived=include_archived)
    return {"code": 0, "data": {"items": items, "total": total,
                                "limit": limit, "offset": offset}}


@router.get("/conversations/{conv_id}")
def get_conversation(conv_id: str):
    conv = conversation.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    conv["created_at"] = str(conv.get("created_at") or "")
    conv["updated_at"] = str(conv.get("updated_at") or "")
    conv["vision"] = conversation.chat_vision_for(conv)
    usage = conversation.usage_total(conv_id)
    return {"code": 0, "data": {
        **conv,
        "messages": conversation.list_messages(conv_id),
        # usage 与 usage_total 同源：新前端读 usage，旧字段保留兼容。
        "usage": usage,
        "usage_total": usage,
    }}


@router.get("/conversations/{conv_id}/usage")
def get_conversation_usage(conv_id: str):
    """会话 token 用量（输入 / 输出 / 速度 / 缓存 / 上下文占用）。

    轻量接口：不返回消息转录，会话管理抽屉与聊天头部轮询都不必拉整段历史。
    """
    if not conversation.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    return {"code": 0, "data": conversation.usage_total(conv_id)}


@router.get("/conversations/{conv_id}/graph")
def get_conversation_graph(conv_id: str):
    """执行画布：主代理 / 团队成员 / 子代理的 DAG 与最近活动。"""
    from services.agent_zero.services import graph_service

    data = graph_service.conversation_graph(conv_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    return {"code": 0, "data": data}


@router.get("/conversations/{conv_id}/messages")
def list_conversation_messages(conv_id: str, limit: int = 200):
    if not conversation.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    return {"code": 0, "data": conversation.list_messages(conv_id, limit=limit)}


@router.get("/conversations/{conv_id}/attachments/{attachment_id}")
def get_conversation_attachment(conv_id: str, attachment_id: str):
    """读取消息附件（图片内联显示 / 文档下载）。"""
    from urllib.parse import quote

    from services.agent_zero.services import attachment_service

    path, meta = attachment_service.resolve_attachment(conv_id, attachment_id)
    if path is None:
        raise HTTPException(status_code=404, detail="附件不存在")
    name = str(meta.get("name") or path.name)
    media_type = str(meta.get("mime") or "application/octet-stream")
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}"},
    )


@router.get("/conversations/{conv_id}/attachments/{attachment_id}/preview")
def preview_conversation_attachment(conv_id: str, attachment_id: str,
                                    max_chars: int = 120_000):
    """附件预览：返回抽取后的文本 / 元数据，前端按类型做 Markdown、表格、HTML、PDF 等渲染。

    原文件仍走 `/attachments/{id}`；HTML 不在同源 iframe 里直接打开，而是
    前端用 sandbox iframe + srcdoc 渲染，避免附件脚本拿到 App 的权限。
    """
    from urllib.parse import quote

    from services.agent_zero.services import attachment_service

    path, meta = attachment_service.resolve_attachment(conv_id, attachment_id)
    if path is None:
        raise HTTPException(status_code=404, detail="附件不存在")
    limit = max(1_000, min(int(max_chars or 120_000), 400_000))
    text = attachment_service.read_attachment_text(conv_id, meta, max_chars=limit)
    return {"code": 0, "data": {
        "id": attachment_id,
        "name": str(meta.get("name") or path.name),
        "mime": str(meta.get("mime") or "application/octet-stream"),
        "size": int(meta.get("size") or path.stat().st_size),
        "kind": str(meta.get("kind") or "file"),
        "text": text,
        "truncated": bool(meta.get("truncated") or len(text) >= limit),
        "url": (f"/api/agent-zero/conversations/{quote(str(conv_id), safe='')}"
                f"/attachments/{quote(str(attachment_id), safe='')}"),
    }}


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: str):
    return {"code": 0, "data": {"deleted": conversation.delete_conversation(conv_id)}}


def _stop_runs_for(conv_ids: List[str]) -> int:
    """批量删除前先停掉这些对话里还活着的 run。

    否则 worker 会继续往已被删除的行上追加事件（append_event 找不到 run 时
    只是静默返回，真正的问题是白跑一轮 LLM 并可能重建节点）。
    """
    from services.agent_zero.task_layer.conv_worker import get_worker

    stopped = 0
    worker = get_worker()
    for cid in conv_ids:
        try:
            run = run_service.active_run(cid)
        except Exception:
            logger.warning("查询对话活跃 run 失败 conv=%s", cid, exc_info=True)
            continue
        if not run:
            continue
        rid = str(run.get("id") or "")
        if not rid:
            continue
        try:
            run_service.request_stop(rid)
            if not worker.force_stop(rid) and run.get("status") == run_service.STATUS_QUEUED:
                run_service.finish_run(rid, run_service.STATUS_STOPPED)
        except Exception:
            logger.warning("批量删除前停止 run 失败 run=%s", rid, exc_info=True)
        stopped += 1
    return stopped


@router.delete("/conversations")
def delete_all_conversations(workspace_id: Optional[str] = None,
                             role_id: Optional[str] = None,
                             include_archived: bool = True,
                             confirm: bool = False):
    """一键删除对话：默认删除某个工作区下的**全部**对话（含已归档）。

    必须显式传 `confirm=true`——这是一次不可撤销的批量删除，路由不做二次确认，
    只挡裸调用。既活跃 run 会在删除前先被停掉。
    """
    if not confirm:
        raise HTTPException(status_code=400, detail="批量删除需要显式确认（confirm=true）")
    ids = conversation.list_conversation_ids(
        workspace_id=workspace_id, role_id=role_id, include_archived=include_archived)
    stopped = _stop_runs_for(ids)
    deleted = conversation.delete_conversations(ids)
    return {"code": 0, "data": {"deleted": deleted, "total": len(ids), "stopped": stopped}}


class ConvPermBody(BaseModel):
    mode: str


class ConvModeBody(BaseModel):
    mode: str


@router.put("/conversations/{conv_id}/permission")
def set_conversation_permission(conv_id: str, body: ConvPermBody):
    """实时保存该会话的权限档位（新会话继承上一条会话的档位）。"""
    from services.agent_zero.task_layer import permissions

    if not conversation.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail="对话不存在")
    try:
        mode = permissions.set_conversation_mode(conv_id, body.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": {"conversation_id": conv_id, "mode": mode}}


@router.put("/conversations/{conv_id}/mode")
def set_conversation_run_mode(conv_id: str, body: ConvModeBody):
    """实时保存该会话的运行模式（chat / agent / goal，默认 agent）。"""
    if not conversation.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail="对话不存在")
    try:
        mode = conversation.set_run_mode(conv_id, body.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": {"conversation_id": conv_id, "mode": mode}}


class ConvPrefsBody(BaseModel):
    model_code: Optional[str] = None
    thinking_level: Optional[str] = None


@router.put("/conversations/{conv_id}/preferences")
def set_conversation_preferences(conv_id: str, body: ConvPrefsBody):
    """会话级模型 / 思考强度：切换后按会话记住，新会话沿用最近一次。"""
    if not conversation.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail="对话不存在")
    try:
        conv = conversation.update_conversation_preferences(
            conv_id, model_code=body.model_code,
            thinking_level=body.thinking_level)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": {
        "conversation_id": conv_id,
        "model_code": conv.get("model_code") or "",
        "thinking_level": conv.get("thinking_level") or "",
    }}


class GoalBody(BaseModel):
    objective: Optional[str] = Field(None, max_length=4000)
    token_budget: Optional[int] = Field(None, ge=0)
    max_runs: Optional[int] = Field(None, ge=0)


@router.get("/conversations/{conv_id}/goal")
def get_conversation_goal(conv_id: str):
    """目标模式：该对话最近一条目标（含 achieved / exhausted / closed）。"""
    from services.agent_zero.task_layer import goal_service

    if not conversation.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    return {"code": 0, "data": goal_service.get_goal(conv_id)}


@router.put("/conversations/{conv_id}/goal")
def put_conversation_goal(conv_id: str, body: GoalBody):
    """创建 / 更新目标（objective / token_budget / max_runs）。"""
    from services.agent_zero.task_layer import goal_service

    if not conversation.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    try:
        goal = goal_service.get_goal(conv_id)
        objective = (body.objective or "").strip()
        # 没有目标、或旧目标已结束且用户给了新目标 → 新建（旧的自动关闭）
        if not goal or (goal.get("status") != "active" and objective):
            if not objective:
                raise HTTPException(status_code=400, detail="缺少目标 objective")
            data = goal_service.set_goal(conv_id, objective,
                                         token_budget=body.token_budget,
                                         max_runs=body.max_runs)
        else:
            data = goal_service.update_goal(
                conv_id, objective=body.objective,
                token_budget=body.token_budget, max_runs=body.max_runs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": data}


@router.delete("/conversations/{conv_id}/goal")
def delete_conversation_goal(conv_id: str):
    """结束目标模式（停止自动续跑；已入队的续跑 run 不再新增）。"""
    from services.agent_zero.task_layer import goal_service

    if not conversation.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    closed = goal_service.close_goal(conv_id)
    if closed:
        conversation.set_run_mode(conv_id, "autonomous")
    return {"code": 0, "data": {"closed": closed,
                                "goal": goal_service.get_goal(conv_id)}}


class TodoBody(BaseModel):
    # content 是主字段（对齐 DSH todo_write）；title 保留兼容旧调用。
    content: Optional[str] = Field(None, max_length=500)
    title: Optional[str] = Field(None, max_length=500)
    status: Optional[str] = Field(
        None, pattern="^(pending|in_progress|completed|open|doing|done)$")
    note: Optional[str] = Field(None, max_length=2000)


class BoundWorkspaceBody(BaseModel):
    workspace_id: Optional[str] = Field(None, max_length=80)


@router.patch("/conversations/{conv_id}/workspace")
def set_conversation_workspace(conv_id: str, body: BoundWorkspaceBody):
    """切换会话工作区（空 = 回到角色 / 群组的默认工作区）。"""
    if not conversation.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    try:
        conv = conversation.set_conversation_workspace(
            conv_id, body.workspace_id,
            follow=not str(body.workspace_id or "").strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    data = dict(conv or {})
    # 兼容旧前端：bound_workspace_id 已是历史字段，空值统一返回 ""。
    data["bound_workspace_id"] = data.get("bound_workspace_id") or ""
    return {"code": 0, "data": data}


@router.post("/conversations/{conv_id}/read")
def mark_conversation_read(conv_id: str):
    """已读上报：打开会话 / 本轮收尾时由前端调用，清掉未读红点。"""
    from services.agent_zero.task_layer import conversation as conv_mod

    if not conv_mod.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    conv_mod.mark_conversation_read(conv_id)
    return {"code": 0, "data": {"conversation_id": conv_id, "unread": False}}


@router.get("/conversations/{conv_id}/todos")
def list_conversation_todos(conv_id: str):
    """任务清单：该对话的待办（进行中 → 待处理 → 已完成 排序）。"""
    from services.agent_zero.services import todo_service

    if not conversation.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    return {"code": 0, "data": todo_service.list_todos(conv_id)}


@router.post("/conversations/{conv_id}/todos")
def add_conversation_todo(conv_id: str, body: TodoBody):
    from services.agent_zero.services import todo_service

    if not conversation.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    try:
        item = todo_service.add_todo(conv_id, body.content or body.title or "",
                                     source="user", note=body.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"code": 0, "data": {"item": item,
                                "items": todo_service.list_todos(conv_id)}}


@router.patch("/conversations/{conv_id}/todos/{todo_id}")
def update_conversation_todo(conv_id: str, todo_id: str, body: TodoBody):
    from services.agent_zero.services import todo_service

    item = todo_service.update_todo(todo_id, conv_id,
                                    content=body.content or body.title,
                                    status=body.status, note=body.note)
    if not item:
        raise HTTPException(status_code=404, detail=f"任务不存在: {todo_id}")
    return {"code": 0, "data": {"item": item,
                                "items": todo_service.list_todos(conv_id)}}


@router.delete("/conversations/{conv_id}/todos/{todo_id}")
def delete_conversation_todo(conv_id: str, todo_id: str):
    from services.agent_zero.services import todo_service

    if not todo_service.delete_todo(todo_id, conv_id):
        raise HTTPException(status_code=404, detail=f"任务不存在: {todo_id}")
    return {"code": 0, "data": {"items": todo_service.list_todos(conv_id)}}


class ProposalBody(BaseModel):
    action: str = Field(..., pattern="^(accept|dismiss)$")
    goal: Optional[str] = None


@router.post("/conversations/{conv_id}/proposals/{node_id}")
async def conv_proposal(conv_id: str, node_id: str, body: ProposalBody):
    if body.action == "dismiss":
        if not conversation.mark_proposal(conv_id, node_id, "dismissed"):
            raise HTTPException(status_code=404, detail=f"执行建议不存在: {node_id}")
        return {"code": 0, "data": {"status": "dismissed"}}
    try:
        run = conversation.accept_proposal(conv_id, node_id, USER, goal=body.goal)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await conv_worker.request_run(run["id"])
    return {"code": 0, "data": run}


@router.get("/conversations/{conv_id}/runs")
def list_conversation_runs(conv_id: str, limit: int = 20,
                           status: Optional[str] = None):
    if not conversation.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    return {"code": 0, "data": run_service.list_runs(conv_id, limit=limit, status=status)}


@router.get("/runs/active")
def active_runs(limit: int = 30):
    """跨工作区活跃 run（queued/running）列表：标题 / 工作区 / 状态 / 待人工处理项。"""
    from services.agent_zero.services import ask_service
    from services.agent_zero.task_layer import permissions

    runs = run_service.list_active_runs(limit=limit)
    for run in runs:
        conv_id = str(run.get("conversation_id") or "")
        pending = ask_service.pending_for(conv_id)
        if pending:
            # 左边栏「运行中」据此显示闪烁的「回答」按钮；只带最小字段。
            run["pending_question"] = {
                "id": pending[0].get("id"),
                "question": str(pending[0].get("question") or "")[:200],
            }
        approvals = [a for a in permissions.list_pending(conv_id)
                     if str(a.get("run_id") or "") == str(run.get("id") or "")]
        if approvals:
            a = approvals[-1]
            run["pending_approval"] = {
                "id": a.get("id"),
                "tool": a.get("tool"),
                "risk": a.get("risk") or "medium",
                "note": str(a.get("note") or "")[:200],
            }
    return {"code": 0, "data": runs}


@router.get("/runs/{run_id}")
def get_run(run_id: str, since: int = 0):
    run = run_service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"运行不存在: {run_id}")
    events = run_service.list_events(run_id, since=since)
    last_seq = events[-1].get("_seq") if events else int(since)
    return {"code": 0, "data": {
        "run": run, "events": events, "last_seq": int(last_seq or 0),
        "terminal": run.get("status") in run_service.TERMINAL_STATUSES,
    }}


@router.get("/runs/{run_id}/stream")
async def run_stream(run_id: str, since: int = 0, interval: float = 0.5):
    """运行事件流 SSE：先按 seq 游标补齐历史，之后事件落库即推送。

    正文流式靠 `run_service` 的进程内总线即时到达（不是每 interval 拉一次库，
    那会让文本一段一段跳）。断线重连与多进程部署仍按游标轮询数据库兜底。
    """
    if not run_service.get_run(run_id):
        raise HTTPException(status_code=404, detail=f"运行不存在: {run_id}")
    iv = max(0.15, min(float(interval or 0.5), 5.0))

    def _frame(run, events, cursor):
        return ("event: run\ndata: "
                + json.dumps({"run": run, "events": events, "last_seq": cursor},
                             ensure_ascii=False) + "\n\n")

    async def _gen():
        cursor = max(0, int(since))
        # 先订阅再补齐：补齐期间落库的事件也在队列里，靠游标去重，不会重复
        queue = run_service.subscribe(run_id)
        idle = 0
        try:
            backlog = run_service.list_events(run_id, since=cursor, limit=1000)
            if backlog:
                cursor = int(backlog[-1].get("_seq") or cursor)
                # 补齐历史时分帧下发（每帧最多 120 条）：整段重放（刷新、事后打开
                # 会话、群聊里好几个角色轮流说）如果塞进一帧，前端会在同一个 tick
                # 里渲染上千段内容——观感就是「等半天之后疯狂输出」，主线程也跟着卡。
                # 分帧后浏览器能边收边画，并且按「发言角色」分组渲染。
                _replay = run_service.get_run(run_id)
                for _i in range(0, len(backlog), 120):
                    part = backlog[_i:_i + 120]
                    # 每帧的 last_seq 必须是**这一帧**的末尾（而不是整段历史的末尾）：
                    # 前端用 last_seq 推进游标，一帧就推到末尾会让后续分帧被整段跳过。
                    yield _frame(_replay if _i == 0 else None, part,
                                 int(part[-1].get("_seq") or cursor))
                    if _i + 120 < len(backlog):
                        # 让出一帧：浏览器才有机会把上一块画出来，而不是把
                        # 上千条事件攒到同一个渲染周期里一次性铺开。
                        await asyncio.sleep(0.01)
            # **已到终态就别再挂着连接**：补齐时 run 可能已经结束（刷新页面 /
            # 事后打开会话 / 排队消息刚跑完）。过去的实现只在 while 里、且只在
            # 「一次轮询没读到事件」的分支才发 run_end——若 run 在补齐阶段就已
            # 结束，连接会一直 ping 到 idle=240（约 2 分钟）才自然断开；前端在
            # 这期间只能干等，表现就是「转圈不动，必须点停止或再发一条才恢复」。
            _end = run_service.get_run(run_id)
            if _end and _end.get("status") in run_service.TERMINAL_STATUSES:
                yield ("event: run_end\ndata: "
                       + json.dumps({"run": _end}, ensure_ascii=False) + "\n\n")
                return

            while idle < 240:
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=iv)
                except asyncio.TimeoutError:
                    # 总线覆盖不到的事件（其它进程写库）或队列漏读：按游标补一次
                    events = run_service.list_events(run_id, since=cursor, limit=500)
                    run = run_service.get_run(run_id)
                    if events:
                        cursor = int(events[-1].get("_seq") or cursor)
                        idle = 0
                        yield _frame(run, events, cursor)
                    else:
                        idle += 1
                        yield ": ping\n\n"
                    if run and run.get("status") in run_service.TERMINAL_STATUSES:
                        yield ("event: run_end\ndata: "
                               + json.dumps({"run": run}, ensure_ascii=False) + "\n\n")
                        break
                    continue

                seq = int(evt.get("_seq") or 0)
                if seq <= cursor:
                    continue
                cursor = seq
                idle = 0
                kind = evt.get("_kind") or ""
                # 正文 delta 不必每帧查一次 run；收尾帧才需要状态
                yield _frame(None if kind in ("delta", "thinking") else run_service.get_run(run_id),
                            [evt], cursor)

                if kind in ("done", "error", "interrupted"):
                    # worker 在 done / interrupted 之后才 finish_run，稍等它落终态
                    run = None
                    for _ in range(8):
                        run = run_service.get_run(run_id)
                        if run and run.get("status") in run_service.TERMINAL_STATUSES:
                            break
                        await asyncio.sleep(0.05)
                    yield ("event: run_end\ndata: "
                           + json.dumps({"run": run}, ensure_ascii=False) + "\n\n")
                    break
            # 循环自然结束（idle 上限 / 上面 break 之后走到这里）：如果 run 已
            # 终态就一定补一帧 run_end，不要让连接静默关闭——前端只能靠「连接
            # 关闭且没有 run_end」去猜，猜错就一直转圈。
            _tail = run_service.get_run(run_id)
            if _tail and _tail.get("status") in run_service.TERMINAL_STATUSES:
                yield ("event: run_end\ndata: "
                       + json.dumps({"run": _tail}, ensure_ascii=False) + "\n\n")
        except Exception as e:                     # 单帧失败不掐断长连接
            logger.debug("运行 SSE 帧异常 run=%s: %s", run_id, e)
            # 异常也不能让前端永远挂着：给出当前真实状态，由前端决定收尾/重连
            try:
                _err_run = run_service.get_run(run_id)
            except Exception:
                _err_run = None
            if _err_run:
                yield ("event: run_end\ndata: "
                       + json.dumps({"run": _err_run}, ensure_ascii=False) + "\n\n")
        finally:
            run_service.unsubscribe(run_id, queue)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: str):
    """强制停止：标记停止并立即取消执行任务，不等待下一个事件间隙。"""
    run = run_service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"运行不存在: {run_id}")
    stopped = run_service.request_stop(run_id)
    from services.agent_zero.task_layer.conv_worker import get_worker

    forced = get_worker().force_stop(run_id)
    if not forced and run.get("status") in (run_service.STATUS_QUEUED,
                                            run_service.STATUS_RUNNING):
        # 没有存活任务可取消：
        # - queued：直接收尾，避免它再被调度；
        # - running：说明执行任务已不在本进程（失联 / 卡在不可取消的同步段），
        #   不能干等 30s 的失联回收，这里立即强制收尾。
        if run.get("status") == run_service.STATUS_QUEUED:
            run_service.finish_run(run_id, run_service.STATUS_STOPPED)
        else:
            get_worker()._force_finalize(run_id)
    return {"code": 0, "data": {"id": run_id,
                                "stop_requested": bool(stopped),
                                "forced": bool(forced)}}


# ── 进程面板：会话内「还在跑的东西」+ 单独停止 ─────────────────────────
#
# 左边栏「进程」面板的数据源。三类进程统一展示：
#   1. 后台工具（前台预算超时转过去的 shell / python / 长任务）
#   2. 子代理（call_subagent / call_agent / call_team 跑出来的子任务）
#   3. 进行中的 run 本身（可从这里停，效果等于停止整轮）
#
# 关键点：**停止单个进程不影响当前会话**——后台工具用 job_id 精确 kill，
# 子代理本身就是后台 job，run 级停止才走 /runs/{id}/stop。


@router.get("/conversations/{conv_id}/processes")
def list_conversation_processes(conv_id: str):
    """该会话当前在跑的进程（前台工具 / 后台任务 / 子代理 / 活动 run）。"""
    from services.agent_zero.task_layer import tool_activity, tool_jobs

    if not conversation.get_conversation(conv_id):
        raise HTTPException(status_code=404, detail=f"对话不存在: {conv_id}")
    items: List[dict] = []
    jobs = tool_jobs.list_jobs(conv_id)
    job_call_ids = {str(j.get("call_id") or "") for j in jobs if j.get("call_id")}
    for job in jobs:
        items.append({
            "id": job["id"],
            "kind": tool_activity.kind_for(str(job.get("name") or "")),
            "name": job.get("name"),
            "status": job.get("status"),
            "elapsed_s": round(max(0.0, time.time()
                                   - float(job.get("started_at") or 0)), 1),
            "started_at": float(job.get("started_at") or 0),
            "args": job.get("args") or {},
            "run_id": job.get("run_id") or "",
            "summary": str(job.get("result") or job.get("error") or "")[:400],
            "stoppable": True,
        })
    # 仍在预算内前台等待的 shell / 子代理：tool_jobs 看不到，但用户同样需要看见。
    # 若同一次调用已经转后台（call_id 相同），以后台 job 为准，避免重复一行。
    for act in tool_activity.list_active(conv_id):
        if act.get("call_id") and str(act["call_id"]) in job_call_ids:
            continue
        items.append(act)
    return {"code": 0, "data": {"items": items}}


@router.post("/processes/{process_id}/stop")
def stop_process(process_id: str, kind: str = "tool"):
    """停止**单个进程**，不影响会话其它部分。

    - kind=tool / subagent：先找后台 job 精确 kill；找不到再取消前台任务
      （前台工具取消后本轮继续，模型会收到「工具已取消」）；
    - kind=run：等价于停止整轮运行（走 /runs/{id}/stop 同一实现）。
    """
    from services.agent_zero.task_layer import tool_activity, tool_jobs

    if (kind or "tool").lower() == "run":
        run = run_service.get_run(process_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"运行不存在: {process_id}")
        return stop_run(process_id)
    job = tool_jobs.get(process_id)
    if job is not None:
        ok = tool_jobs.kill(process_id, reason="用户从进程面板手动停止")
        return {"code": 0, "data": {"id": process_id, "stopped": bool(ok),
                                    "status": (tool_jobs.get(process_id) or {}).get("status")}}
    if tool_activity.kill(process_id):
        return {"code": 0, "data": {"id": process_id, "stopped": True,
                                    "status": "cancelling"}}
    raise HTTPException(status_code=404, detail=f"进程不存在或已结束: {process_id}")


# ── 危险操作审批 ────────────────────────────────────────────────────────

class ApprovalBody(BaseModel):
    action: str = Field(..., description="approve_once | approve_session | approve_role | approve_team | approve_always | deny")


@router.get("/conversations/{conv_id}/approvals")
def list_conv_approvals(conv_id: str, status: Optional[str] = None):
    from services.agent_zero.task_layer import permissions

    if status:
        from sqlalchemy import text

        from services.agent_zero.database import get_session_local
        from services.agent_zero.stores.zero_store import _q

        SessionLocal = get_session_local()
        with SessionLocal() as db:
            rows = db.execute(
                text(f"SELECT * FROM {_q('tool_approvals')} "
                     f"WHERE conversation_id = :c AND status = :s "
                     f"ORDER BY created_at DESC LIMIT 50"),
                {"c": conv_id, "s": status},
            ).mappings().all()
        items = [dict(r) for r in rows]
    else:
        items = permissions.list_pending(conv_id)
    return {"code": 0, "data": {"items": items}}


class AskAnswerBody(BaseModel):
    selected: Optional[List[str]] = None
    other: Optional[str] = None
    ignored: bool = False


@router.get("/conversations/{conv_id}/questions")
def list_pending_questions(conv_id: str):
    from services.agent_zero.services import ask_service

    return {"code": 0, "data": {"items": ask_service.pending_for(conv_id)}}


@router.get("/questions/{question_id}")
def get_question(question_id: str):
    from services.agent_zero.services import ask_service

    data = ask_service.get(question_id)
    if not data:
        raise HTTPException(status_code=404, detail="问题不存在或已结束")
    return {"code": 0, "data": data}


@router.post("/questions/{question_id}")
async def answer_question(question_id: str, body: AskAnswerBody):
    from services.agent_zero.services import ask_service

    result = ask_service.answer(question_id, body.model_dump())
    if result is None:
        raise HTTPException(status_code=404, detail="问题不存在或已结束")
    return {"code": 0, "data": result}


@router.post("/approvals/{approval_id}")
async def decide_approval(approval_id: str, body: ApprovalBody):
    """处理审批。

    必须是 async 路由：审批等待方用 asyncio.Event 唤醒；同步路由会被 FastAPI
    丢到线程池执行，跨线程 set() 不能可靠唤醒运行中的事件循环，表现为点了
    「允许」但工具迟迟不继续。
    """
    from services.agent_zero.task_layer import permissions

    action = (body.action or "").strip()
    if action not in ("approve_once", "approve_session", "approve_role",
                      "approve_team", "approve_always", "deny"):
        raise HTTPException(status_code=400, detail=f"非法审批动作: {body.action}")
    try:
        result = permissions.decide_approval(approval_id, action)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"code": 0, "data": result}


