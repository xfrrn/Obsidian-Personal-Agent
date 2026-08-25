"""将内部 TurnEvent 映射为保持兼容的 CLI/Web Event 协议。"""

from __future__ import annotations

import json

import asyncio

from agent.core.turn.events import (
    AssistantDelta,
    AssistantResponseReceived,
    RuntimeShutdown,
    PlanUpdated,
    ToolApprovalRequested,
    ToolRequested,
    ToolResult,
    ToolResultStatus,
    TurnError,
    TurnEvent,
    TurnFinished,
    TurnInterrupted,
    TurnStarted,
)
from agent.protocol.event import Event, EventKind
from agent.tools.invocation import ToolInvocation


class PublicEventAdapter:
    """将可靠 TurnEvent 同步映射到一个不丢失的入口事件流。"""

    def __init__(self) -> None:
        # 可靠映射不能反压 turn loop；入口若停止消费，其生命周期由所属运行时负责结束。
        self._events: asyncio.Queue[Event] = asyncio.Queue()

    def __call__(self, event: TurnEvent) -> None:
        public_event = self.adapt(event)
        if public_event is not None:
            self._events.put_nowait(public_event)

    async def receive(self) -> Event:
        return await self._events.get()

    @staticmethod
    def adapt(event: TurnEvent) -> Event | None:
        match event:
            case TurnStarted(submission_id=submission_id, mode=mode):
                return Event(
                    EventKind.TURN_STARTED,
                    data={"submission_id": submission_id, "mode": mode.value},
                )
            case AssistantDelta(text=text, channel="reasoning"):
                return Event(EventKind.ASSISTANT_MESSAGE, text, {"delta": True, "reasoning_delta": True})
            case AssistantDelta(text=text):
                return Event(EventKind.ASSISTANT_MESSAGE, text, {"delta": True})
            case AssistantResponseReceived(response=response, streamed=streamed):
                if response.content or response.reasoning or response.tool_calls:
                    data = {"replace": True} if streamed else {}
                    data["is_final"] = not response.tool_calls and response.end_turn is not False
                    if response.reasoning:
                        data["reasoning"] = response.reasoning
                    if response.tool_calls:
                        # 工具调用仅在完整响应到达时可见；此前已发送的 delta 不能被当作最终结论。
                        data["tool_calls_pending"] = True
                    return Event(EventKind.ASSISTANT_MESSAGE, response.content or "", data)
            case ToolRequested(invocation=invocation):
                return Event(
                    EventKind.TOOL_CALL,
                    invocation.name,
                    {"call_id": invocation.call_id, "arguments": invocation.arguments},
                )
            case ToolApprovalRequested(
                submission_id=submission_id,
                invocation=invocation,
                justification=justification,
            ):
                return Event(
                    EventKind.APPROVAL_REQUESTED,
                    "工具请求一次宿主执行权限。",
                    {
                        "submission_id": submission_id,
                        "call_id": invocation.call_id,
                        "name": invocation.name,
                        "command": _approval_command(invocation),
                        "justification": justification,
                    },
                )
            case ToolResult(call_id=call_id, name=name, content=content, status=status):
                return Event(
                    EventKind.TOOL_RESULT,
                    content,
                    {
                        "call_id": call_id,
                        "name": name,
                        "status": status.value,
                        "is_error": status is not ToolResultStatus.SUCCESS,
                    },
                )
            case PlanUpdated(submission_id=submission_id, update=update):
                return Event(
                    EventKind.PLAN_UPDATED,
                    update.explanation or "",
                    {"submission_id": submission_id, **update.as_dict()},
                )
            case TurnFinished():
                return Event(EventKind.TURN_FINISHED)
            case TurnInterrupted():
                return Event(EventKind.TURN_INTERRUPTED, "回合已中断。")
            case TurnError(message=message):
                return Event(EventKind.ERROR, message)
            case RuntimeShutdown():
                return Event(EventKind.SHUTDOWN)
        return None


def _approval_command(invocation: ToolInvocation) -> str:
    command = invocation.arguments.get("command")
    if isinstance(command, str):
        return command
    if invocation.name == "obsidian_command":
        command_id = invocation.arguments.get("command_id")
        if isinstance(command_id, str):
            return f"obsidian command id={command_id}"
    if invocation.name.startswith("mcp__"):
        return json.dumps(
            invocation.arguments, ensure_ascii=False, indent=2
        )[:2_000]
    return ""
