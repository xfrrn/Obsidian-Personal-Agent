"""Control-operation regression checks for the submission scheduler."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agent.config.settings import Settings
from agent.core.handle import AgentHandle
from agent.core.loop import submission_loop
from agent.core.scheduling.input_queue import InputQueue
from agent.core.session import Session
from agent.core.turn.public_events import PublicEventAdapter
from agent.llm.types import AssistantResponse, ToolCall
from agent.protocol.event import Event, EventKind
from agent.protocol.mode import ModeKind
from agent.protocol.op import CancelTool, Interrupt, UserInput
from agent.permissions import ToolAccess
from agent.skills.service import SkillsService
from agent.tools.registry import ToolRegistry
from agent.tools.router import ToolRouter
from agent.tools.runtime import ToolCallRuntime
from agent.tools.types import ToolSpec


class BlockingTool:
    required_access = ToolAccess.READ_ONLY
    spec = ToolSpec(name="block", description="blocks until cancelled", parameters={"type": "object"})

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.was_cancelled = False

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> str:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.was_cancelled = True
            raise


class ToolThenReplyClient:
    def __init__(self) -> None:
        self.calls = 0
        self.tool_result: dict[str, Any] | None = None

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AssistantResponse:
        self.calls += 1
        if self.calls == 1:
            return AssistantResponse(None, (ToolCall("call-1", "block", {}),))
        self.tool_result = messages[-1]
        return AssistantResponse("工具取消后继续回答")


class BlockingClient:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.was_cancelled = False

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AssistantResponse:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.was_cancelled = True
            raise


class InterruptedToolHistoryClient:
    def __init__(self) -> None:
        self.calls = 0
        self.tool_results: list[dict[str, Any]] = []

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AssistantResponse:
        self.calls += 1
        if self.calls == 1:
            return AssistantResponse(None, (
                ToolCall("running-call", "block", {}),
                ToolCall("pending-call", "never-runs", {}),
            ))
        self.tool_results = [message for message in messages if message.get("role") == "tool"]
        return AssistantResponse("下一轮可继续")


class SchedulingControlTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_tool_keeps_the_turn_running(self) -> None:
        tool = BlockingTool()
        client = ToolThenReplyClient()
        handle, events, loop_task = self._start_session(client, [tool])
        try:
            handle.submit(UserInput("调用工具"))
            await asyncio.wait_for(tool.started.wait(), timeout=1)
            handle.submit(CancelTool("call-1"))

            turn_events = await self._events_until(events, EventKind.TURN_FINISHED)
        finally:
            handle.shutdown()
            await asyncio.wait_for(loop_task, timeout=1)

        self.assertTrue(tool.was_cancelled)
        self.assertEqual(client.tool_result, {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "block",
            "content": "工具调用已取消。",
        })
        self.assertIn(EventKind.TOOL_RESULT, [event.kind for event in turn_events])
        self.assertNotIn(EventKind.TURN_INTERRUPTED, [event.kind for event in turn_events])

    async def test_interrupt_cancels_the_matching_active_turn(self) -> None:
        client = BlockingClient()
        handle, events, loop_task = self._start_session(client, [])
        try:
            turn_id = handle.submit(UserInput("持续运行"))
            await asyncio.wait_for(client.started.wait(), timeout=1)
            handle.submit(Interrupt(turn_id))

            turn_events = await self._events_until(events, EventKind.TURN_INTERRUPTED)
        finally:
            handle.shutdown()
            await asyncio.wait_for(loop_task, timeout=1)

        self.assertTrue(client.was_cancelled)
        self.assertNotIn(EventKind.TURN_FINISHED, [event.kind for event in turn_events])

    async def test_interrupt_completes_pending_tool_history(self) -> None:
        tool = BlockingTool()
        client = InterruptedToolHistoryClient()
        handle, events, loop_task = self._start_session(client, [tool])
        try:
            turn_id = handle.submit(UserInput("先调用两个工具"))
            await asyncio.wait_for(tool.started.wait(), timeout=1)
            handle.submit(Interrupt(turn_id))
            interrupted_events = await self._events_until(events, EventKind.TURN_INTERRUPTED)

            handle.submit(UserInput("继续"))
            await self._events_until(events, EventKind.TURN_FINISHED)
        finally:
            handle.shutdown()
            await asyncio.wait_for(loop_task, timeout=1)

        self.assertEqual(client.tool_results, [
            {
                "role": "tool",
                "tool_call_id": "running-call",
                "name": "block",
                "content": "工具调用因回合中断而未完成。",
            },
            {
                "role": "tool",
                "tool_call_id": "pending-call",
                "name": "never-runs",
                "content": "工具调用因回合中断而未完成。",
            },
        ])
        self.assertEqual(
            [event.kind for event in interrupted_events].count(EventKind.TOOL_RESULT), 2
        )

    def _start_session(
        self, client: object, handlers: list[object]
    ) -> tuple[AgentHandle, PublicEventAdapter, asyncio.Task[None]]:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = Settings(
            api_key=None,
            model="test-model",
            base_url="http://unused",
            system_prompt="test",
            workspace=Path(directory.name),
            shell_enabled=False,
            request_timeout_seconds=1,
            max_tool_rounds=2,
        )
        handle = AgentHandle()
        tools = ToolRegistry(handlers)
        router = ToolRouter(tools)
        session = Session(
            config=settings,
            client=client,
            tools=tools,
            tool_router=router,
            tool_runtime=ToolCallRuntime(router),
            skills_service=SkillsService(Path(directory.name) / "skills"),
            context_contributors=(),
            input_queue=InputQueue(),
            turn_events=handle.turn_events,
        )
        events = PublicEventAdapter()
        handle.turn_events.subscribe(events)
        return handle, events, asyncio.create_task(submission_loop(session, handle))

    async def _events_until(self, adapter: PublicEventAdapter, terminal: EventKind) -> list[Event]:
        events: list[Event] = []
        while terminal not in [event.kind for event in events]:
            events.append(await asyncio.wait_for(adapter.receive(), timeout=1))
        return events


if __name__ == "__main__":
    unittest.main()
