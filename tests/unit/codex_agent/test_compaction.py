"""End-to-end check for automatic context compaction."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agent.config.settings import Settings
from agent.core.agent_loop import run_turn
from agent.core.handle import AgentHandle
from agent.core.loop import create_session
from agent.core.turn.context import TurnContext
from agent.llm.types import AssistantResponse, ClientError, ContextLimitError, TokenUsage


class CompactingClient:
    def __init__(self, fail_compaction: bool = False) -> None:
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
        self._fail_compaction = fail_compaction

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AssistantResponse:
        self.calls.append((messages, tools))
        if len(self.calls) == 1:
            if self._fail_compaction:
                raise ClientError("summary unavailable")
            return AssistantResponse("旧对话摘要", usage=TokenUsage(100, 10, 110))
        return AssistantResponse("最终答复", usage=TokenUsage(12, 4, 16))


class ContextLimitClient:
    def __init__(self) -> None:
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AssistantResponse:
        self.calls.append((messages, tools))
        if len(self.calls) == 1:
            raise ContextLimitError("context_length_exceeded")
        if len(self.calls) == 2:
            return AssistantResponse("超限压缩摘要")
        return AssistantResponse("重试成功")


class AutoCompactionTest(unittest.IsolatedAsyncioTestCase):
    async def test_context_limit_compacts_and_retries_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                api_key=None,
                model="test-model",
                base_url="http://unused",
                system_prompt="test",
                workspace=Path(directory),
                shell_enabled=False,
                request_timeout_seconds=1,
                max_tool_rounds=1,
                context_window_tokens=20_000,
                reserved_output_tokens=100,
                auto_compact_token_limit=19_000,
            )
            client = ContextLimitClient()
            session = create_session(settings, AgentHandle(), client)
            session.history.extend(
                [
                    {"role": "user", "content": "旧请求"},
                    {"role": "assistant", "content": "旧回答"},
                ]
            )

            await run_turn(
                session,
                TurnContext(1, "新请求", settings.system_prompt, 1),
            )

        self.assertEqual(len(client.calls), 3)
        self.assertEqual(client.calls[1][1], [])
        self.assertEqual(session.context_window.summary, "超限压缩摘要")
        self.assertEqual(
            [message["content"] for message in session.history],
            ["新请求", "重试成功"],
        )

    async def test_compacts_old_window_and_preserves_latest_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                api_key=None,
                model="test-model",
                base_url="http://unused",
                system_prompt="test",
                workspace=Path(directory),
                shell_enabled=False,
                request_timeout_seconds=1,
                max_tool_rounds=1,
                context_window_tokens=20_000,
                reserved_output_tokens=100,
                auto_compact_token_limit=200,
            )
            client = CompactingClient()
            session = create_session(settings, AgentHandle(), client)
            session.history.extend(
                [
                    {"role": "user", "content": "旧请求"},
                    {"role": "assistant", "content": "x" * 500},
                ]
            )

            await run_turn(
                session,
                TurnContext(
                    submission_id=1,
                    user_text="新请求",
                    system_prompt=settings.system_prompt,
                    max_tool_rounds=1,
                ),
            )

        self.assertEqual(len(client.calls), 2)
        compaction_messages, compaction_tools = client.calls[0]
        self.assertEqual(compaction_tools, [])
        self.assertIn("旧请求", compaction_messages[-1]["content"])
        self.assertEqual(session.context_window.number, 1)
        self.assertEqual(session.context_window.summary, "旧对话摘要")
        self.assertEqual(session.token_counter.reported_request_count, 2)
        self.assertEqual(session.token_counter.total_tokens, 126)
        self.assertEqual([message["content"] for message in session.history], ["新请求", "最终答复"])
        normal_messages, _ = client.calls[1]
        self.assertIn("旧对话摘要", normal_messages[-2]["content"])

    async def test_compaction_failure_falls_back_to_the_normal_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                api_key=None,
                model="test-model",
                base_url="http://unused",
                system_prompt="test",
                workspace=Path(directory),
                shell_enabled=False,
                request_timeout_seconds=1,
                max_tool_rounds=1,
                context_window_tokens=20_000,
                reserved_output_tokens=100,
                auto_compact_token_limit=200,
            )
            client = CompactingClient(fail_compaction=True)
            session = create_session(settings, AgentHandle(), client)
            session.history.extend(
                [
                    {"role": "user", "content": "旧请求"},
                    {"role": "assistant", "content": "x" * 500},
                ]
            )

            await run_turn(
                session,
                TurnContext(
                    submission_id=1,
                    user_text="新请求",
                    system_prompt=settings.system_prompt,
                    max_tool_rounds=1,
                ),
            )

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(session.context_window.number, 0)
        self.assertEqual(session.token_counter.reported_request_count, 1)
        self.assertEqual(client.calls[-1][0][-1]["content"], "新请求")


if __name__ == "__main__":
    unittest.main()
