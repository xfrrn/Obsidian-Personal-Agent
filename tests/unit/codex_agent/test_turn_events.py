"""TurnEventBus 的协议投影、旁路 mailbox 与历史补全检查。"""

from __future__ import annotations

import unittest

from agent.core.turn.bus import MailboxClosed, TurnEventBus
from agent.core.turn.conversation import ConversationHistory, INTERRUPTED_TOOL_CONTENT
from agent.core.turn.events import (
    AssistantDelta,
    AssistantResponseReceived,
    ToolApprovalRequested,
    ToolRequested,
    ToolResult,
    ToolResultStatus,
    RuntimeShutdown,
    TurnFinished,
    TurnStarted,
)
from agent.core.turn.public_events import PublicEventAdapter
from agent.llm.types import AssistantResponse, ToolCall
from agent.protocol.event import Event, EventKind
from agent.tools.invocation import ToolInvocation


class TurnEventBusTest(unittest.IsolatedAsyncioTestCase):
    async def test_approval_event_exposes_command_but_keeps_structured_identity(self) -> None:
        adapter = PublicEventAdapter()
        invocation = ToolInvocation("call-1", "exec_command", {"command": "echo ok"})

        adapter(ToolApprovalRequested(7, invocation, "需要宿主环境"))

        self.assertEqual(
            await adapter.receive(),
            Event(
                EventKind.APPROVAL_REQUESTED,
                "工具请求一次宿主执行权限。",
                {
                    "submission_id": 7,
                    "call_id": "call-1",
                    "name": "exec_command",
                    "command": "echo ok",
                    "justification": "需要宿主环境",
                },
            ),
        )

    async def test_adapter_preserves_public_event_shape(self) -> None:
        invocation = ToolInvocation("call-1", "read_file", {"path": "README.md"})
        bus = TurnEventBus()
        adapter = PublicEventAdapter()
        bus.subscribe(adapter)

        bus.emit(TurnStarted(7, "read the file"))
        bus.emit(AssistantDelta(7, "reading"))
        bus.emit(AssistantResponseReceived(7, AssistantResponse("done"), streamed=False))
        bus.emit(ToolRequested(7, invocation))
        bus.emit(
            ToolResult(
                7,
                invocation.call_id,
                invocation.name,
                "cancelled",
                ToolResultStatus.INTERRUPTED,
            )
        )
        bus.emit(TurnFinished(7))
        events = [await adapter.receive() for _ in range(5)]

        self.assertEqual(
            events,
            [
            Event(
                EventKind.TURN_STARTED,
                data={"submission_id": 7, "mode": "default"},
            ),
                Event(EventKind.ASSISTANT_MESSAGE, "done", {"is_final": True}),
                Event(
                    EventKind.TOOL_CALL,
                    "read_file",
                    {"call_id": "call-1", "arguments": {"path": "README.md"}},
                ),
                Event(
                    EventKind.TOOL_RESULT,
                    "cancelled",
                    {
                        "call_id": "call-1",
                        "name": "read_file",
                        "status": "interrupted",
                        "is_error": True,
                    },
                ),
                Event(EventKind.TURN_FINISHED),
            ],
        )

    async def test_streamed_response_reliably_replaces_dropped_deltas(self) -> None:
        bus = TurnEventBus()
        adapter = PublicEventAdapter()
        bus.subscribe(adapter)

        bus.emit(AssistantDelta(7, "part"))
        bus.emit(AssistantResponseReceived(7, AssistantResponse("complete"), streamed=True))

        self.assertEqual(
            await adapter.receive(),
                Event(EventKind.ASSISTANT_MESSAGE, "complete", {"replace": True, "is_final": True}),
        )

    async def test_listener_can_opt_into_stream_deltas(self) -> None:
        bus = TurnEventBus()
        adapter = PublicEventAdapter()
        bus.subscribe(adapter, include_deltas=True)

        bus.emit(AssistantDelta(7, "part"))

        self.assertEqual(
            await adapter.receive(),
            Event(EventKind.ASSISTANT_MESSAGE, "part", {"delta": True}),
        )

    async def test_listener_can_opt_into_reasoning_deltas(self) -> None:
        bus = TurnEventBus()
        adapter = PublicEventAdapter()
        bus.subscribe(adapter, include_deltas=True)

        bus.emit(AssistantDelta(7, "检查", "reasoning"))

        self.assertEqual(
            await adapter.receive(),
            Event(EventKind.ASSISTANT_MESSAGE, "检查", {"delta": True, "reasoning_delta": True}),
        )

    async def test_tool_call_response_marks_streamed_text_as_provisional(self) -> None:
        bus = TurnEventBus()
        adapter = PublicEventAdapter()
        bus.subscribe(adapter)

        bus.emit(
            AssistantResponseReceived(
                7,
                AssistantResponse(
                    "created",
                    (ToolCall("call-1", "exec_command", {"command": "dir"}),),
                    reasoning="检查目录",
                ),
                streamed=True,
            )
        )

        self.assertEqual(
            await adapter.receive(),
            Event(
                EventKind.ASSISTANT_MESSAGE,
                "created",
                {"replace": True, "is_final": False, "reasoning": "检查目录", "tool_calls_pending": True},
            ),
        )

    async def test_follow_up_without_tool_call_is_not_final(self) -> None:
        bus = TurnEventBus()
        adapter = PublicEventAdapter()
        bus.subscribe(adapter)

        bus.emit(AssistantResponseReceived(7, AssistantResponse("continue", end_turn=False), streamed=False))

        self.assertEqual(
            await adapter.receive(),
            Event(EventKind.ASSISTANT_MESSAGE, "continue", {"is_final": False}),
        )

    async def test_mailbox_drops_when_full_and_closes_waiters(self) -> None:
        bus = TurnEventBus()
        mailbox = bus.watch(capacity=1)
        first = TurnFinished(1)

        bus.emit(first)
        bus.emit(TurnFinished(2))

        self.assertEqual(await mailbox.receive(), first)
        self.assertEqual(mailbox.dropped, 1)
        bus.close()
        with self.assertRaises(MailboxClosed):
            await mailbox.receive()

    async def test_close_keeps_already_delivered_shutdown_event(self) -> None:
        bus = TurnEventBus()
        mailbox = bus.watch()
        shutdown = RuntimeShutdown()

        bus.emit(shutdown)
        bus.close()

        self.assertIs(await mailbox.receive(), shutdown)
        with self.assertRaises(MailboxClosed):
            await mailbox.receive()


class ConversationHistoryTest(unittest.TestCase):
    def test_interruption_completes_only_unfinished_tool_calls(self) -> None:
        history = ConversationHistory()
        history.append_assistant(
            AssistantResponse(
                None,
                (
                    ToolCall("finished", "first", {}),
                    ToolCall("pending", "second", {}),
                ),
            )
        )
        history.append_tool_result("finished", "first", "ok")

        self.assertEqual(history.complete_interrupted_tools()[0].call_id, "pending")
        self.assertEqual(
            history.messages[-1],
            {
                "role": "tool",
                "tool_call_id": "pending",
                "name": "second",
                "content": INTERRUPTED_TOOL_CONTENT,
            },
        )


if __name__ == "__main__":
    unittest.main()
