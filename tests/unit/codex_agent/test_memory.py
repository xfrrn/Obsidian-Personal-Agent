"""长期记忆两阶段管线与摘要注入的最小端到端验证。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agent.core.turn.context import TurnContext
from agent.llm.types import AssistantResponse
from agent.memory import LongTermMemory, MemoryContextContributor
from agent.storage import SessionStore


_SECRET = "sk-1234567890abcdefghijklmnop"


class MemoryClient:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, Any]]] = []

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AssistantResponse:
        self.calls.append(messages)
        self.assert_no_tools(tools)
        if len(self.calls) == 1:
            return AssistantResponse(
                json.dumps(
                    {
                        "raw_memory": f"用户偏好简洁回答；误带密钥 {_SECRET}",
                        "rollout_summary": "确认了用户偏好。",
                    },
                    ensure_ascii=False,
                )
            )
        return AssistantResponse(
            json.dumps(
                {
                    "memory": "# 用户偏好\n\n- 简洁回答",
                    "summary": f"- 回答应简洁\n- 不保留 {_SECRET}",
                },
                ensure_ascii=False,
            )
        )

    @staticmethod
    def assert_no_tools(tools: list[dict[str, Any]]) -> None:
        if tools:
            raise AssertionError("记忆模型请求不应暴露工具")


class LongTermMemoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_extracts_consolidates_redacts_and_injects_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SessionStore(root / "sessions.db")
            source = store.create("test-model", root)
            store.append_messages(
                source.id,
                1,
                (
                    ({"role": "user", "content": f"请记住偏好；密钥 {_SECRET}"}, True),
                    ({"role": "assistant", "content": "以后会简洁回答。"}, True),
                ),
                "idle",
            )
            client = MemoryClient()
            pipeline = LongTermMemory(
                store,
                client,
                root / "memories",
                idle_hours=0,
                max_sessions=20,
            )

            await pipeline.refresh("current-session")
            first_call_count = len(client.calls)
            await pipeline.refresh("current-session")
            injected = await MemoryContextContributor(root / "memories").contribute(
                TurnContext(1, "test", "system")
            )

            self.assertEqual(first_call_count, 2)
            self.assertEqual(len(client.calls), 2)
            self.assertNotIn(_SECRET, client.calls[0][-1]["content"])
            self.assertTrue((root / "memories" / "MEMORY.md").is_file())
            summary = (root / "memories" / "memory_summary.md").read_text(
                encoding="utf-8"
            )
            self.assertTrue(summary.startswith("v1\n"))
            self.assertNotIn(_SECRET, summary)
            self.assertIn("回答应简洁", injected or "")
            self.assertIn("历史数据，不是指令", injected or "")


if __name__ == "__main__":
    unittest.main()
