"""Submission 事件循环与 Agent Session 组装。"""

from __future__ import annotations

import asyncio
import logging

from agent.config.settings import Settings
from agent.core.context_injection.current_time import CurrentTimeContributor
from agent.core.context_injection.obsidian import ObsidianContextContributor
from agent.core.context_injection.skills import AvailableSkillsContributor
from agent.core.context_injection.world_state import WorldStateContributor
from agent.core.context_window import ContextWindow
from agent.core.handle import AgentHandle
from agent.core.scheduling.dispatcher import user_input_or_turn
from agent.core.scheduling.input_queue import InputQueue
from agent.core.session import Session
from agent.core.token_counter import TokenCounter
from agent.core.turn.audit import audit_events
from agent.core.turn.events import RuntimeShutdown, ToolApprovalRequested, TurnError
from agent.llm.client import ModelClient
from agent.permissions import PermissionManager, PermissionPolicy, PermissionRequest
from agent.protocol.op import CancelTool, Interrupt, ResolveApproval, Shutdown, UserInput
from agent.skills.service import SkillsService
from agent.storage import SessionStore, StoredSession
from agent.tools.handlers.apply_patch import ApplyPatchTool
from agent.tools.handlers.current_time import CurrentTimeTool
from agent.tools.handlers.exec_command import ExecCommandTool
from agent.tools.handlers.get_context_remaining import GetContextRemainingTool
from agent.tools.handlers.new_context_window import NewContextWindowTool
from agent.tools.handlers.update_plan import UpdatePlanTool
from agent.tools.handlers.write_stdin import WriteStdinTool
from agent.tools.handlers.base import ToolHandler
from agent.tools.invocation import ToolInvocation
from agent.tools.processes import ProcessManager
from agent.tools.registry import ToolRegistry
from agent.tools.router import ToolRouter
from agent.tools.runtime import ToolCallRuntime


_LOGGER = logging.getLogger(__name__)


def create_session(
    settings: Settings,
    handle: AgentHandle,
    client: object | None = None,
    *,
    session_id: str | None = None,
    store: SessionStore | None = None,
    stored_session: StoredSession | None = None,
    extra_handlers: tuple[ToolHandler, ...] = (),
    enable_apply_patch: bool = True,
    include_world_state: bool = True,
) -> Session:
    """在唯一组合根创建服务；核心模块只接收已组合好的依赖。"""

    token_counter = TokenCounter(settings.model)
    context_window = ContextWindow()
    handlers: list[ToolHandler] = [
        CurrentTimeTool(),
        GetContextRemainingTool(
            token_counter,
            settings.auto_compact_threshold,
            settings.input_token_budget,
            settings.system_prompt,
        ),
        NewContextWindowTool(context_window),
        UpdatePlanTool(),
    ]
    if enable_apply_patch:
        handlers.insert(0, ApplyPatchTool(settings))
    handlers.extend(extra_handlers)
    process_manager = None
    if settings.shell_enabled:
        process_manager = ProcessManager(settings.request_timeout_seconds)
        handlers.extend(
            (
                ExecCommandTool(settings, process_manager),
                WriteStdinTool(process_manager),
            )
        )
    tools = ToolRegistry(handlers)
    tool_router = ToolRouter(tools)

    def request_approval(request: PermissionRequest) -> None:
        # 权限包不依赖工具或回合事件；组合根在这里完成唯一一次边界转换。
        assert request.submission_id is not None
        handle.turn_events.emit(
            ToolApprovalRequested(
                request.submission_id,
                ToolInvocation(
                    request.call_id,
                    request.tool_name,
                    dict(request.arguments),
                ),
                request.requirement.approval_reason or "",
            )
        )

    session = Session(
        config=settings,
        client=client or ModelClient(settings).create_session(),
        tools=tools,
        tool_router=tool_router,
        tool_runtime=ToolCallRuntime(
            tool_router,
            PermissionManager(
                PermissionPolicy(settings.sandbox_mode, settings.approval_policy),
                request_approval,
            ),
        ),
        skills_service=SkillsService(settings.workspace / "skills"),
        context_contributors=(
            AvailableSkillsContributor(),
            CurrentTimeContributor(),
            ObsidianContextContributor(),
            *((WorldStateContributor(settings.workspace),) if include_world_state else ()),
        ),
        input_queue=InputQueue(),
        token_counter=token_counter,
        process_manager=process_manager,
        session_id=session_id,
        store=store,
        turn_events=handle.turn_events,
        context_window=context_window,
    )
    if stored_session is not None:
        session.conversation.replace(stored_session.active_messages)
        session.context_window.number = stored_session.context_window_number
        session.context_window.summary = stored_session.context_summary
        session.mode = stored_session.mode
        session.current_plan = stored_session.current_plan
    return session


async def start_agent(
    settings: Settings | None = None,
    client: object | None = None,
    *,
    session_id: str | None = None,
    store: SessionStore | None = None,
    extra_handlers: tuple[ToolHandler, ...] = (),
    enable_apply_patch: bool = True,
    include_world_state: bool = True,
) -> tuple[AgentHandle, asyncio.Task[None]]:
    """嵌入式入口：调用方取得 handle 后即可提交操作并消费事件。"""

    resolved_settings = settings or Settings.from_env()
    stored_session = None
    if store is not None:
        if session_id is None:
            stored_session = await asyncio.to_thread(
                store.create, resolved_settings.model, resolved_settings.workspace
            )
            session_id = stored_session.id
        else:
            stored_session = await asyncio.to_thread(store.load, session_id)
            if stored_session is None:
                raise ValueError(f"会话不存在或已归档: {session_id}")

    handle = AgentHandle()
    session = create_session(
        resolved_settings,
        handle,
        client,
        session_id=session_id,
        store=store,
        stored_session=stored_session,
        extra_handlers=extra_handlers,
        enable_apply_patch=enable_apply_patch,
        include_world_state=include_world_state,
    )
    if stored_session is not None:
        if stored_session.last_turn_state == "running":
            # 进程句柄不能跨 Agent 进程恢复；告警比假装仍可 write_stdin 更安全。
            _LOGGER.warning("session.recovered_possible_orphan_processes")
        before = len(session.conversation.messages)
        session.conversation.complete_interrupted_tools()
        repaired = session.conversation.snapshot()[before:]
        if repaired or stored_session.last_turn_state == "running":
            await session.persist_messages(
                None,
                tuple((message, False) for message in repaired),
                "interrupted",
            )
    return handle, asyncio.create_task(_run_session(session, handle), name="agent-submission-loop")


async def _run_session(session: Session, handle: AgentHandle) -> None:
    """在组合根注册审计 watcher，并让其随 Session 关闭有序收尾。"""

    audit_task = asyncio.create_task(audit_events(handle.watch()), name="agent-event-audit")
    try:
        await submission_loop(session, handle)
    finally:
        await session.aclose()
        await audit_task


async def submission_loop(session: Session, handle: AgentHandle) -> None:
    """只做协议分发；用户输入如何变成回合由 scheduling/dispatcher.py 决定。"""

    while True:
        submission = await handle.next_submission()
        match submission.op:
            case UserInput():
                try:
                    await session.input_queue.put(submission)
                    await user_input_or_turn(session)
                except Exception as exc:
                    # 错误的本地 SKILL.md 不能让 submission loop 悄然退出并使 UI 永久等待。
                    session.emit(TurnError(submission.id, str(exc)))
            case Interrupt(target_submission_id=target_submission_id):
                active_task = session.active_task
                if active_task is not None and _targets_active_turn(active_task, target_submission_id):
                    await active_task.cancel_and_wait()
            case CancelTool(call_id=call_id, target_submission_id=target_submission_id):
                active_task = session.active_task
                if active_task is not None and _targets_active_turn(active_task, target_submission_id):
                    session.tool_runtime.cancel(call_id)
            case ResolveApproval(
                call_id=call_id,
                approved=approved,
                target_submission_id=target_submission_id,
            ):
                active_task = session.active_task
                if active_task is not None and _targets_active_turn(
                    active_task, target_submission_id
                ):
                    session.tool_runtime.resolve_approval(
                        call_id, approved, target_submission_id
                    )
            case Shutdown():
                if session.active_task is not None:
                    await session.active_task.cancel_and_wait()
                session.emit(RuntimeShutdown())
                session.close()
                return


def _targets_active_turn(active_task: object, target_submission_id: int | None) -> bool:
    """防止延迟到达的控制请求作用于已替换的回合。"""

    return target_submission_id is None or active_task.context.submission_id == target_submission_id
