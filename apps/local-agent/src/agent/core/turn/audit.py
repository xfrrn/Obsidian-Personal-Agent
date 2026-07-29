"""可丢弃的 TurnEvent 审计 watcher；只记录安全元数据。"""

from __future__ import annotations

import asyncio
import logging

from agent.core.turn.bus import Mailbox, MailboxClosed
from agent.core.turn.events import (
    AssistantDelta,
    AssistantResponseReceived,
    ImplicitSkillInvocation,
    PlanUpdated,
    RuntimeShutdown,
    ToolApprovalRequested,
    ToolRequested,
    ToolResult,
    TurnError,
    TurnEvent,
    TurnFinished,
    TurnInterrupted,
    TurnStarted,
)


_LOGGER = logging.getLogger(__name__)


async def audit_events(mailbox: Mailbox) -> None:
    """消费 mailbox，审计失败或积压均不能反向影响回合。"""

    while True:
        try:
            event = await mailbox.receive()
        except MailboxClosed:
            return
        try:
            name, fields = _record_for(event)
            # 标准 logging 可能阻塞在文件或 stderr；旁路工作改在线程中完成。
            await asyncio.to_thread(_LOGGER.info, name, extra=fields)
        except Exception:
            # watcher 是 best-effort：日志后端故障不重试，也不终止 Agent。
            continue


def _record_for(event: TurnEvent) -> tuple[str, dict[str, str | int | bool]]:
    """只提取关联 ID、状态和长度；绝不把提示词、参数或结果写入审计流。"""

    match event:
        case TurnStarted(submission_id=submission_id):
            return "audit.turn_started", {"submission_id": submission_id}
        case AssistantDelta(submission_id=submission_id, text=text):
            return "audit.assistant_delta", {
                "submission_id": submission_id,
                "delta_chars": len(text),
            }
        case AssistantResponseReceived(submission_id=submission_id, streamed=streamed):
            return "audit.assistant_response", {
                "submission_id": submission_id,
                "streamed": streamed,
            }
        case ToolRequested(submission_id=submission_id, invocation=invocation):
            return "audit.tool_requested", {
                "submission_id": submission_id,
                "tool_name": invocation.name,
                "tool_call_id": invocation.call_id,
            }
        case ToolApprovalRequested(submission_id=submission_id, invocation=invocation):
            return "audit.tool_approval_requested", {
                "submission_id": submission_id,
                "tool_name": invocation.name,
                "tool_call_id": invocation.call_id,
            }
        case ToolResult(
            submission_id=submission_id,
            call_id=call_id,
            name=name,
            status=status,
        ):
            return "audit.tool_result", {
                "submission_id": submission_id,
                "tool_name": name,
                "tool_call_id": call_id,
                "tool_status": status.value,
                "is_error": status.value != "success",
            }
        case ImplicitSkillInvocation(
            submission_id=submission_id,
            call_id=call_id,
            skill_name=skill_name,
        ):
            return "audit.skill_invocation", {
                "submission_id": submission_id,
                "tool_call_id": call_id,
                "skill_name": skill_name,
            }
        case PlanUpdated(submission_id=submission_id, update=update):
            return "audit.plan_updated", {
                "submission_id": submission_id,
                "plan_steps": len(update.plan),
            }
        case TurnFinished(submission_id=submission_id):
            return "audit.turn_finished", {"submission_id": submission_id}
        case TurnInterrupted(submission_id=submission_id):
            return "audit.turn_interrupted", {"submission_id": submission_id}
        case TurnError(submission_id=submission_id):
            return "audit.turn_error", _submission_field(submission_id)
        case RuntimeShutdown():
            return "audit.runtime_shutdown", {}
    raise AssertionError(f"unknown turn event: {type(event).__name__}")


def _submission_field(submission_id: int | None) -> dict[str, int]:
    return {} if submission_id is None else {"submission_id": submission_id}
