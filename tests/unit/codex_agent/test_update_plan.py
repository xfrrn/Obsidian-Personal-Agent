"""update_plan 的校验、事件、持久化与恢复链路。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agent.config.settings import Settings
from agent.core.loop import start_agent
from agent.core.turn.public_events import PublicEventAdapter
from agent.llm.types import AssistantResponse, ToolCall
from agent.protocol.event import EventKind
from agent.protocol.mode import ModeKind
from agent.protocol.op import UserInput
from agent.storage import SessionStore
from agent.tools.handlers.update_plan import UpdatePlanTool


class _PlanningClient:
    def __init__(self) -> None:
        self.requests: list[list[dict[str, Any]]] = []

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AssistantResponse:
        self.requests.append(messages)
        if len(self.requests) == 1:
            return AssistantResponse(
                None,
                (
                    ToolCall(
                        "plan-1",
                        "update_plan",
                        {
                            "explanation": "先定位，再验证",
                            "plan": [
                                {"step": "定位调用链", "status": "completed"},
                                {"step": "运行端到端验证", "status": "in_progress"},
                            ],
                        },
                    ),
                ),
            )
        return AssistantResponse("验证完成")


class UpdatePlanTest(unittest.IsolatedAsyncioTestCase):
    async def test_handler_rejects_invalid_snapshots_and_plan_mode(self) -> None:
        tool = UpdatePlanTool()

        with self.assertRaisesRegex(ValueError, "最多只能有一个"):
            await tool.run(
                {
                    "plan": [
                        {"step": "one", "status": "in_progress"},
                        {"step": "two", "status": "in_progress"},
                    ]
                }
            )
        with self.assertRaisesRegex(PermissionError, "Plan Mode"):
            await tool.run(
                {"plan": [{"step": "one", "status": "pending"}]},
                mode=ModeKind.PLAN,
            )

    async def test_end_to_end_emits_and_restores_current_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(
                api_key=None,
                model="test-model",
                base_url="http://unused",
                system_prompt="test",
                workspace=root,
                shell_enabled=False,
                request_timeout_seconds=1,
                max_tool_rounds=2,
                session_db_path=root / "sessions.db",
            )
            store = SessionStore(settings.session_db_path)
            stored = store.create(settings.model, root)
            client = _PlanningClient()
            handle, runner = await start_agent(
                settings, client, session_id=stored.id, store=store
            )
            events = PublicEventAdapter()
            handle.turn_events.subscribe(events)
            handle.submit(UserInput("完成多步骤任务"))

            received = []
            while not received or received[-1].kind is not EventKind.TURN_FINISHED:
                received.append(await asyncio.wait_for(events.receive(), timeout=2))

            handle.shutdown()
            await runner
            restored = store.load(stored.id)

        assert restored is not None and restored.current_plan is not None
        plan_event = next(event for event in received if event.kind is EventKind.PLAN_UPDATED)
        self.assertEqual(plan_event.text, "先定位，再验证")
        self.assertEqual(plan_event.data["plan"][1]["status"], "in_progress")
        self.assertEqual(restored.current_plan.as_dict(), {
            "explanation": "先定位，再验证",
            "plan": [
                {"step": "定位调用链", "status": "completed"},
                {"step": "运行端到端验证", "status": "in_progress"},
            ],
        })
        self.assertEqual(restored.messages[-2].payload["content"], "Plan updated")
        self.assertIn("update_plan", str(client.requests[1]))


if __name__ == "__main__":
    unittest.main()
