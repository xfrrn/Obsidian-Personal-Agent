"""时间查询、上下文余量和手动窗口切换工具的最小验证。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent.config.loader import build_mode_system_prompt
from agent.config.settings import Settings
from agent.core.agent_loop import run_turn
from agent.core.context_window import ContextWindow
from agent.core.handle import AgentHandle
from agent.core.loop import create_session
from agent.core.token_counter import TokenCounter
from agent.core.turn.context import TurnContext
from agent.llm.types import AssistantResponse, TokenUsage, ToolCall
from agent.protocol.mode import ModeKind
from agent.tools.handlers.current_time import CurrentTimeTool
from agent.tools.handlers.get_context_remaining import GetContextRemainingTool
from agent.tools.handlers.new_context_window import NewContextWindowTool


class ContextToolsTest(unittest.IsolatedAsyncioTestCase):
    async def test_time_and_remaining_return_stable_json(self) -> None:
        fixed_time = datetime(
            2026, 7, 27, 18, 30, tzinfo=timezone(timedelta(hours=8))
        )
        current_time = json.loads(
            await CurrentTimeTool(lambda: fixed_time).run({})
        )

        counter = TokenCounter("test-model")
        messages = [{"role": "user", "content": "test"}]
        counter.record_response(messages, [], TokenUsage(100, 5, 105))
        remaining = json.loads(
            await GetContextRemainingTool(counter, 500, 1_000).run({})
        )

        self.assertEqual(
            current_time, {"current_time": "2026-07-27 10:30:00 UTC"}
        )
        self.assertEqual(
            remaining,
            {"tokens_left": 900, "tokens_until_compaction": 400},
        )

        tool = GetContextRemainingTool(counter, 5_000, 10_000, "system")
        prefix_tokens = counter.estimate_request(
            [
                {
                    "role": "system",
                    "content": build_mode_system_prompt("system", ModeKind.DEFAULT),
                }
            ],
            [],
        )
        body_remaining = json.loads(
            await tool.run({"mode": "body_after_prefix"})
        )
        self.assertEqual(
            body_remaining,
            {
                "tokens_left": 9_900 - prefix_tokens,
                "tokens_until_compaction": 4_900 - prefix_tokens,
            },
        )

    async def test_new_window_request_is_one_shot(self) -> None:
        context_window = ContextWindow()

        result = await NewContextWindowTool(context_window).run({})

        self.assertIn("下一次模型调用前", result)
        self.assertTrue(context_window.consume_new_request())
        self.assertFalse(context_window.consume_new_request())

    async def test_manual_request_compacts_after_tool_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                api_key=None,
                model="test-model",
                base_url="http://unused",
                system_prompt="test",
                workspace=Path(directory),
                shell_enabled=False,
                request_timeout_seconds=1,
                context_window_tokens=20_000,
                reserved_output_tokens=100,
                auto_compact_token_limit=19_000,
            )
            client = ManualCompactionClient()
            session = create_session(settings, AgentHandle(), client)
            session.history.extend(
                [
                    {"role": "user", "content": "旧请求"},
                    {"role": "assistant", "content": "旧答复"},
                ]
            )

            await run_turn(
                session,
                TurnContext(1, "新请求", settings.system_prompt),
            )

        self.assertEqual(len(client.calls), 3)
        self.assertEqual(client.calls[1][1], [])
        self.assertIn("旧请求", client.calls[1][0][-1]["content"])
        self.assertEqual(session.context_window.number, 1)
        self.assertEqual(session.context_window.summary, "手动压缩摘要")
        self.assertFalse(session.context_window.new_window_requested)
        self.assertEqual(session.history[-1]["content"], "完成")

    async def test_session_registers_context_tools_by_default(self) -> None:
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
            session = create_session(settings, AgentHandle(), object())

        self.assertEqual(
            [handler.spec.name for handler in session.tools.handlers()],
            [
                "apply_patch",
                "current_time",
                "get_context_remaining",
                "new_context_window",
                "obsidian_command",
                "update_plan",
            ],
        )


class ManualCompactionClient:
    def __init__(self) -> None:
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AssistantResponse:
        self.calls.append((messages, tools))
        if len(self.calls) == 1:
            return AssistantResponse(
                None,
                (ToolCall("call-window", "new_context_window", {}),),
            )
        if len(self.calls) == 2:
            return AssistantResponse("手动压缩摘要")
        return AssistantResponse("完成")


if __name__ == "__main__":
    unittest.main()
