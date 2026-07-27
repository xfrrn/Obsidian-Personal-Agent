"""不用网络或第三方依赖的 MVP 端到端自检。"""

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
from agent.core.turn.context import TurnContext
from agent.core.turn.bus import TurnEventBus
from agent.core.agent_loop import run_turn
from agent.core.turn.public_events import PublicEventAdapter
from agent.llm.types import AssistantResponse, ToolCall
from agent.protocol.event import EventKind
from agent.protocol.mode import ModeKind
from agent.protocol.op import UserInput
from agent.permissions import ToolAccess
from agent.skills.service import SkillsService
from agent.tools.registry import ToolRegistry
from agent.tools.router import ToolRouter
from agent.tools.runtime import ToolCallRuntime
from agent.tools.types import ToolSpec


class EchoTool:
    """测试替身：确认模型请求经过 registry，再回到下一次模型调用。"""

    required_access = ToolAccess.READ_ONLY
    spec = ToolSpec(
        name="echo",
        description="回显文本",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
    )

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> str:
        return f"echo:{arguments['text']}"


class ParallelTool:
    """仅用于验证同一模型响应中的显式并行工具不会被串行调度。"""

    supports_parallel_tool_calls = True
    required_access = ToolAccess.READ_ONLY
    spec = ToolSpec(name="parallel", description="runs in parallel", parameters={"type": "object"})

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self._running = 0

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> str:
        self._running += 1
        if self._running == 2:
            self.started.set()
        try:
            await self.release.wait()
        finally:
            self._running -= 1
        return "done"


class FakeClient:
    """第一次请求工具，第二次读取工具结果后生成最终回复。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AssistantResponse:
        self.calls += 1
        if self.calls == 1:
            self.assert_tools = tools
            return AssistantResponse(None, (ToolCall("call-1", "echo", {"text": "hello"}),))
        assert messages[-1] == {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "echo",
            "content": "echo:hello",
        }
        return AssistantResponse("工具返回：echo:hello")


class ParallelClient:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AssistantResponse:
        self.calls += 1
        if self.calls == 1:
            return AssistantResponse(
                None,
                (ToolCall("call-1", "parallel", {}), ToolCall("call-2", "parallel", {})),
            )
        return AssistantResponse("done")


class FollowUpClient:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AssistantResponse:
        self.calls += 1
        if self.calls == 1:
            return AssistantResponse("继续生成", end_turn=False)
        return AssistantResponse("完成")


class LongToolChainClient:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AssistantResponse:
        self.calls += 1
        if self.calls <= 6:
            return AssistantResponse(
                None,
                (ToolCall(f"call-{self.calls}", "echo", {"text": str(self.calls)}),),
            )
        return AssistantResponse("完成")


class MvpFlowTest(unittest.IsolatedAsyncioTestCase):
    async def test_end_turn_false_continues_without_a_tool_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                api_key=None,
                model="test-model",
                base_url="http://unused",
                system_prompt="test",
                workspace=Path(directory),
                shell_enabled=False,
                request_timeout_seconds=1,
            )
            client = FollowUpClient()
            tools = ToolRegistry()
            router = ToolRouter(tools)
            session = Session(
                config=settings,
                client=client,
                tools=tools,
                tool_router=router,
                tool_runtime=ToolCallRuntime(router),
                skills_service=SkillsService(Path(directory) / "skills"),
                context_contributors=(),
                input_queue=InputQueue(),
                turn_events=TurnEventBus(),
            )
            await run_turn(session, TurnContext(1, "run", "test", (), ()))

        self.assertEqual(client.calls, 2)

    async def test_tool_chain_continues_until_model_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                api_key=None,
                model="test-model",
                base_url="http://unused",
                system_prompt="test",
                workspace=Path(directory),
                shell_enabled=False,
                request_timeout_seconds=1,
            )
            client = LongToolChainClient()
            tools = ToolRegistry([EchoTool()])
            router = ToolRouter(tools)
            session = Session(
                config=settings,
                client=client,
                tools=tools,
                tool_router=router,
                tool_runtime=ToolCallRuntime(router),
                skills_service=SkillsService(Path(directory) / "skills"),
                context_contributors=(),
                input_queue=InputQueue(),
                turn_events=TurnEventBus(),
            )
            await run_turn(session, TurnContext(1, "run", "test"))

        self.assertEqual(client.calls, 7)
        self.assertEqual(session.history[-1]["content"], "完成")

    async def test_explicit_parallel_tools_start_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                api_key=None,
                model="test-model",
                base_url="http://unused",
                system_prompt="test",
                workspace=Path(directory),
                shell_enabled=False,
                request_timeout_seconds=1,
            )
            tool = ParallelTool()
            tools = ToolRegistry([tool])
            router = ToolRouter(tools)
            self.assertTrue(tools.supports_parallel_tool_calls("parallel"))
            session = Session(
                config=settings,
                client=ParallelClient(),
                tools=tools,
                tool_router=router,
                tool_runtime=ToolCallRuntime(router),
                skills_service=SkillsService(Path(directory) / "skills"),
                context_contributors=(),
                input_queue=InputQueue(),
                turn_events=TurnEventBus(),
            )
            turn = asyncio.create_task(
                run_turn(
                    session,
                    TurnContext(1, "run", "test", (), ()),
                )
            )
            await asyncio.wait_for(tool.started.wait(), timeout=1)
            tool.release.set()
            await asyncio.wait_for(turn, timeout=1)

        self.assertEqual(
            [message for message in session.conversation.messages if message["role"] == "tool"],
            [
                {"role": "tool", "tool_call_id": "call-1", "name": "parallel", "content": "done"},
                {"role": "tool", "tool_call_id": "call-2", "name": "parallel", "content": "done"},
            ],
        )

    async def test_input_reaches_model_then_tool_then_final_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                api_key=None,
                model="test-model",
                base_url="http://unused",
                system_prompt="test",
                workspace=Path(directory),
                shell_enabled=False,
                request_timeout_seconds=1,
            )
            handle = AgentHandle()
            client = FakeClient()
            tools = ToolRegistry([EchoTool()])
            router = ToolRouter(tools)
            self.assertFalse(tools.supports_parallel_tool_calls("echo"))
            session = Session(
                config=settings,
                client=client,
                tools=tools,
                tool_router=router,
                tool_runtime=ToolCallRuntime(router),
                skills_service=SkillsService(Path(directory) / "skills"),
                context_contributors=(),
                input_queue=InputQueue(),
                turn_events=handle.turn_events,
            )
            events = PublicEventAdapter()
            handle.turn_events.subscribe(events)
            loop_task = asyncio.create_task(submission_loop(session, handle))
            handle.submit(UserInput("请回显 hello"))

            kinds: list[EventKind] = []
            texts: list[str] = []
            received = []
            while EventKind.TURN_FINISHED not in kinds:
                event = await asyncio.wait_for(events.receive(), timeout=1)
                received.append(event)
                kinds.append(event.kind)
                texts.append(event.text)

            handle.shutdown()
            await asyncio.wait_for(loop_task, timeout=1)

        self.assertEqual(client.calls, 2)
        self.assertEqual(kinds, [
            EventKind.TURN_STARTED,
            EventKind.ASSISTANT_MESSAGE,
            EventKind.TOOL_CALL,
            EventKind.TOOL_RESULT,
            EventKind.ASSISTANT_MESSAGE,
            EventKind.TURN_FINISHED,
        ])
        self.assertEqual(received[1].data, {"is_final": False, "tool_calls_pending": True})
        self.assertIn("工具返回：echo:hello", texts)


if __name__ == "__main__":
    unittest.main()
