"""Skill 目录、显式 mention 与 Agent 提示词注入的端到端验证。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agent.config.settings import Settings
from agent.core.handle import AgentHandle
from agent.core.loop import create_session, submission_loop
from agent.core.turn.public_events import PublicEventAdapter
from agent.llm.types import AssistantResponse
from agent.protocol.event import EventKind
from agent.protocol.op import UserInput


class RecordingClient:
    """记录模型实际看到的消息，确认 Skill 是上下文而非 Tool。"""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.tools: list[dict[str, Any]] = []

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AssistantResponse:
        self.messages = messages
        self.tools = tools
        return AssistantResponse("已按 Skill 处理")


class SkillInjectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_is_visible_but_only_explicit_skill_body_is_injected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _write_skill(
                workspace / "skills" / "code-review" / "SKILL.md",
                "code-review",
                "审查 Python 代码",
                "先列出严重问题，再给出最小修复建议。",
            )
            _write_skill(
                workspace / "skills" / "secret-workflow" / "SKILL.md",
                "secret-workflow",
                "不应自动加载的工作流",
                "这段正文绝不能在未显式提及时进入提示词。",
            )
            settings = Settings(
                api_key=None,
                model="test-model",
                base_url="http://unused",
                system_prompt="test system",
                workspace=workspace,
                shell_enabled=False,
                request_timeout_seconds=1,
            )
            handle = AgentHandle()
            client = RecordingClient()
            session = create_session(settings, handle, client)
            events = PublicEventAdapter()
            handle.turn_events.subscribe(events)
            loop_task = asyncio.create_task(submission_loop(session, handle))
            handle.submit(UserInput("请用 $code-review 审查这段代码"))

            kinds: list[EventKind] = []
            while EventKind.TURN_FINISHED not in kinds:
                kinds.append((await asyncio.wait_for(events.receive(), timeout=1)).kind)
            handle.shutdown()
            await asyncio.wait_for(loop_task, timeout=1)

        system_context = "\n".join(
            message["content"] for message in client.messages if message["role"] == "system"
        )
        self.assertIn("$code-review: 审查 Python 代码", system_context)
        self.assertIn("$secret-workflow: 不应自动加载的工作流", system_context)
        self.assertIn("先列出严重问题", system_context)
        self.assertNotIn("这段正文绝不能", system_context)
        self.assertEqual(
            [tool["function"]["name"] for tool in client.tools],
            [
                "apply_patch",
                "create_frontmatter",
                "current_time",
                "get_context_remaining",
                "mutate_task",
                "new_context_window",
                "obsidian_command",
                "query_tasks",
                "update_plan",
                "update_properties",
            ],
        )


def _write_skill(path: Path, name: str, description: str, body: str) -> None:
    """测试夹具写入真实 SKILL.md，覆盖 loader 的文件扫描边界。"""

    path.parent.mkdir(parents=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )
