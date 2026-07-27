"""单个回合的 Agent 核心循环：提示词 -> 模型 -> 工具 -> 模型。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
import inspect
import logging
import time
from typing import Any, cast

from agent.core.compaction import CompactionError, compact_history
from agent.core.prompt import (
    build_messages,
    split_history_for_compaction,
)
from agent.core.session import Session
from agent.core.token_counter import ContextBudgetError
from agent.core.turn.context import TurnContext
from agent.core.turn.events import (
    AssistantDelta,
    AssistantResponseReceived,
    PlanUpdated,
    ToolRequested,
    ToolResult,
    ToolResultStatus,
    TurnError,
    TurnFinished,
    TurnInterrupted,
    TurnStarted,
)
from agent.core.turn.task import SessionTask
from agent.utils.logging import log_context
from agent.llm.types import AssistantResponse, ClientError, ContextLimitError, ToolCall
from agent.protocol.mode import ModeKind
from agent.skills.injection import build_skill_injections
from agent.tools.invocation import ToolInvocation
from agent.tools.types import ToolExecution


_LOGGER = logging.getLogger(__name__)


class AgentTurnTask(SessionTask):
    """当前唯一的 SessionTask 实现，将 Agent 循环适配给通用调度器。"""

    def __init__(self, session: Session, context: TurnContext) -> None:
        self._session = session
        self._context = context

    async def run(self) -> None:
        await run_turn(self._session, self._context)


async def run_turn(session: Session, context: TurnContext) -> None:
    """Bind the existing submission ID to every diagnostic emitted by this turn."""

    with log_context(submission_id=context.submission_id):
        await _run_turn(session, context)


async def _run_turn(session: Session, context: TurnContext) -> None:
    """运行一个上下文已冻结的用户回合，并仅通过 TurnEventBus 对外暴露状态。"""

    started_at = time.monotonic()
    _LOGGER.info("turn.started")
    try:
        user_message = session.conversation.append_user(
            context.user_text, context.file_references
        )
        await session.persist_messages(
            context.submission_id, ((user_message, True),), "running"
        )
        # 监控只需知道实际注入了哪些 Skill，不能为此暴露用户提示词或 Skill 正文。
        session.emit(
            TurnStarted(
                context.submission_id,
                context.user_text,
                tuple(skill.name for skill in context.mentioned_skills),
                context.mode,
            )
        )
        context_messages = await _build_context_messages(session, context)
        tool_specs = session.tool_router.model_visible_specs()
        round_number = 0
        # 固定轮数上限会截断合法的长工具链；回合由模型终止信号、取消或上下文预算结束。
        while True:
            round_number += 1
            await _auto_compact_if_needed(session, context, context_messages, tool_specs)
            compacted_summary = session.context_window.render_summary()
            messages = build_messages(
                context.system_prompt,
                session.conversation.snapshot(),
                context_messages,
                compacted_summary,
            )
            if session.token_counter.estimate_request(messages, tool_specs) > session.config.input_token_budget:
                raise ContextBudgetError("自动压缩后当前请求仍超过输入 token 预算。")
            request_started_at = time.monotonic()
            try:
                try:
                    response, streamed = await _complete_turn(
                        session, context.submission_id, messages, tool_specs
                    )
                except ContextLimitError:
                    if not await _auto_compact_if_needed(
                        session,
                        context,
                        context_messages,
                        tool_specs,
                        force=True,
                    ):
                        raise
                    messages = build_messages(
                        context.system_prompt,
                        session.conversation.snapshot(),
                        context_messages,
                        session.context_window.render_summary(),
                    )
                    if session.token_counter.estimate_request(
                        messages, tool_specs
                    ) > session.config.input_token_budget:
                        raise ContextBudgetError(
                            "上下文超限压缩后当前请求仍超过输入 token 预算。"
                        )
                    _LOGGER.info(
                        "llm.context_limit_retry", extra={"round": round_number}
                    )
                    response, streamed = await _complete_turn(
                        session, context.submission_id, messages, tool_specs
                    )
            except ClientError as exc:
                _LOGGER.warning(
                    "llm.failed",
                    extra={"round": round_number, "duration_ms": _elapsed_ms(request_started_at), "error_type": type(exc).__name__},
                )
                raise
            usage_fields = _usage_log_fields(response)
            _LOGGER.info(
                "llm.completed",
                extra={
                    "round": round_number,
                    "duration_ms": _elapsed_ms(request_started_at),
                    "streamed": streamed,
                    **usage_fields,
                },
            )
            session.token_counter.record_response(messages, tool_specs, response.usage)
            needs_follow_up = _needs_follow_up(response)
            assistant_message = session.conversation.append_assistant(response)
            await session.persist_messages(
                context.submission_id,
                ((assistant_message, not needs_follow_up),),
                "running" if needs_follow_up else "idle",
            )
            session.emit(AssistantResponseReceived(context.submission_id, response, streamed))
            if not needs_follow_up:
                _LOGGER.info("turn.finished", extra={"duration_ms": _elapsed_ms(started_at)})
                session.emit(TurnFinished(context.submission_id))
                return

            if response.tool_calls:
                await _run_tool_calls(
                    session, context.submission_id, response.tool_calls, context.mode
                )
    except asyncio.CancelledError:
        _LOGGER.info("turn.cancelled", extra={"duration_ms": _elapsed_ms(started_at)})
        before = len(session.conversation.messages)
        interrupted_results = session.conversation.complete_interrupted_tools()
        repaired = session.conversation.snapshot()[before:]
        try:
            await session.persist_messages(
                context.submission_id,
                tuple((message, False) for message in repaired),
                "interrupted",
            )
        except Exception as persistence_error:
            _LOGGER.error(
                "persistence.failed",
                extra={"error_type": type(persistence_error).__name__},
            )
        for result in interrupted_results:
            session.emit(
                ToolResult(
                    context.submission_id,
                    result.call_id,
                    result.name,
                    result.content,
                    ToolResultStatus.INTERRUPTED,
                )
            )
        session.emit(TurnInterrupted(context.submission_id))
        raise
    except Exception as exc:
        _LOGGER.error("turn.failed", extra={"duration_ms": _elapsed_ms(started_at), "error_type": type(exc).__name__})
        before = len(session.conversation.messages)
        interrupted_results = session.conversation.complete_interrupted_tools()
        repaired = session.conversation.snapshot()[before:]
        # 报告原始错误优先；磁盘故障不应在异常处理阶段再次遮蔽它。
        with suppress(Exception):
            await session.persist_messages(
                context.submission_id,
                tuple((message, False) for message in repaired),
                "failed",
            )
        for result in interrupted_results:
            session.emit(
                ToolResult(
                    context.submission_id,
                    result.call_id,
                    result.name,
                    result.content,
                    ToolResultStatus.INTERRUPTED,
                )
            )
        session.emit(TurnError(context.submission_id, str(exc)))


def _usage_log_fields(response: AssistantResponse) -> dict[str, int]:
    """Record server token counts when supplied, without logging any message content."""

    if response.usage is None:
        return {}
    return {
        "prompt_tokens": response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
        "total_tokens": response.usage.total_tokens,
    }


def _elapsed_ms(started_at: float) -> int:
    return round((time.monotonic() - started_at) * 1000)


def _needs_follow_up(response: AssistantResponse) -> bool:
    """对齐 Codex：工具调用或端点明确未结束时，才继续同一用户 turn。"""

    return bool(response.tool_calls) or response.end_turn is False


async def _run_tool_calls(
    session: Session,
    submission_id: int,
    calls: tuple[ToolCall, ...],
    mode: ModeKind = ModeKind.DEFAULT,
) -> None:
    """并发提交同一响应的调用；运行时按 Handler 的声明隔离有副作用的工具。"""

    invocations = tuple(
        session.tool_router.build_invocation(call.id, call.name, call.arguments) for call in calls
    )
    for invocation in invocations:
        session.emit(ToolRequested(submission_id, invocation))

    executions = await asyncio.gather(
        *(
            _execute_tool(session, submission_id, invocation, mode)
            for invocation in invocations
        )
    )
    # 保持模型响应中的顺序，避免不同兼容端点对 tool 消息顺序的差异造成问题。
    completed = []
    for invocation, (execution, status) in zip(invocations, executions, strict=True):
        message = session.conversation.append_tool_result(
            invocation.call_id, invocation.name, execution.content
        )
        completed.append((invocation, execution, status, message))
    # 同一模型响应若包含多次更新，以调用顺序中的最后一个完整快照为当前状态。
    plan_update = next(
        (
            execution.plan_update
            for _, execution, status, _ in reversed(completed)
            if status is ToolResultStatus.SUCCESS and execution.plan_update is not None
        ),
        None,
    )
    await session.persist_messages(
        submission_id,
        tuple((message, False) for _, _, _, message in completed),
        "running",
        plan_update,
    )
    for invocation, execution, status, _ in completed:
        session.emit(
            ToolResult(
                submission_id,
                invocation.call_id,
                invocation.name,
                execution.content,
                status,
            )
        )
        if status is ToolResultStatus.SUCCESS and execution.plan_update is not None:
            session.emit(PlanUpdated(submission_id, execution.plan_update))


async def _execute_tool(
    session: Session,
    submission_id: int,
    invocation: ToolInvocation,
    mode: ModeKind = ModeKind.DEFAULT,
) -> tuple[ToolExecution, ToolResultStatus]:
    tool_started_at = time.monotonic()
    with log_context(tool_name=invocation.name, tool_call_id=invocation.call_id):
        execution = await session.tool_runtime.execute(invocation, submission_id, mode)
        status = (
            ToolResultStatus.INTERRUPTED
            if execution.interrupted
            else ToolResultStatus.ERROR
            if execution.is_error
            else ToolResultStatus.SUCCESS
        )
        event_name = f"tool.{status.value}"
        log = _LOGGER.warning if status is ToolResultStatus.ERROR else _LOGGER.info
        log(
            event_name,
            extra={"duration_ms": _elapsed_ms(tool_started_at), "is_error": execution.is_error},
        )
    return execution, status


async def _complete_turn(
    session: Session,
    submission_id: int,
    messages: list[dict[str, Any]],
    tool_specs: list[dict[str, Any]],
) -> tuple[AssistantResponse, bool]:
    """优先使用客户端的流式能力；测试替身仍可只实现 complete()。"""

    stream_complete = getattr(session.client, "stream_complete", None)
    if not callable(stream_complete):
        return await session.client.complete(messages, tool_specs), False

    async def publish_delta(text: str) -> None:
        session.emit(AssistantDelta(submission_id, text))

    async def publish_reasoning_delta(text: str) -> None:
        session.emit(AssistantDelta(submission_id, text, "reasoning"))

    typed_stream_complete = cast(
        Callable[..., Awaitable[AssistantResponse]], stream_complete
    )
    if _accepts_reasoning_delta(stream_complete):
        return await typed_stream_complete(
            messages,
            tool_specs,
            publish_delta,
            on_reasoning_delta=publish_reasoning_delta,
        ), True
    return await typed_stream_complete(messages, tool_specs, publish_delta), True


def _accepts_reasoning_delta(stream_complete: object) -> bool:
    try:
        parameters = inspect.signature(stream_complete).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        or parameter.name == "on_reasoning_delta"
        for parameter in parameters
    )


async def _build_context_messages(session: Session, context: TurnContext) -> tuple[str, ...]:
    """目录与正文分两段构造，确保未被点名的 Skill 永远不占用模型上下文。"""

    contributions = await asyncio.gather(
        *(contributor.contribute(context) for contributor in session.context_contributors)
    )
    catalog = tuple(message for message in contributions if message is not None)
    plan_context = ()
    if session.current_plan is not None and not session.current_plan.is_complete:
        steps = "\n".join(
            f"- [{step.status.value}] {step.step}" for step in session.current_plan.plan
        )
        plan_context = (f"## 当前执行计划（运行时状态）\n{steps}",)
    return (*catalog, *plan_context, *build_skill_injections(context.mentioned_skills))


async def _auto_compact_if_needed(
    session: Session,
    context: TurnContext,
    context_messages: tuple[str, ...],
    tool_specs: list[dict[str, Any]],
    *,
    force: bool = False,
) -> bool:
    """在超限前压缩旧窗口；失败时保留原始历史并阻止超限请求。"""

    compacted_summary = session.context_window.render_summary()
    active_tokens = session.token_counter.estimate_request(
        build_messages(
            context.system_prompt,
            session.conversation.snapshot(),
            context_messages,
            compacted_summary,
        ),
        tool_specs,
    )
    manual_compaction = session.context_window.consume_new_request()
    if (
        not force
        and not manual_compaction
        and active_tokens < session.config.auto_compact_threshold
    ):
        return False

    history_to_compact, retained_history = split_history_for_compaction(session.conversation.snapshot())
    if not history_to_compact:
        return False
    try:
        summary = await compact_history(
            session.client,
            session.context_window.summary,
            history_to_compact,
            session.token_counter,
            session.config.input_token_budget,
        )
    except (ClientError, ContextBudgetError, CompactionError):
        return False

    session.conversation.replace(retained_history)
    session.context_window.start_new(summary)
    await session.persist_compaction()
    _LOGGER.info(
        "context.compacted",
        extra={"summary_bytes": len(summary.encode("utf-8")), "history_message_count": len(history_to_compact)},
    )
    return True
