"""Frontmatter 创建工具的最小行为检查。"""

from __future__ import annotations

from datetime import date
import json
import tempfile
import unittest
from pathlib import Path

from agent.changes import ChangeJournal
from agent.config.settings import Settings
from agent.core.handle import AgentHandle
from agent.core.loop import create_session
from agent.permissions import SandboxMode, ToolAccess
from agent.protocol.mode import ModeKind
from agent.tools.handlers.create_frontmatter import CreateFrontmatterTool
from agent.tools.invocation import ToolInvocation


class CreateFrontmatterToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_prepends_canonical_frontmatter_without_changing_body(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            note = workspace / "FastAPI学习.md"
            note.write_text("# FastAPI学习\n\n正文\n", encoding="utf-8")
            tool = CreateFrontmatterTool(
                _settings(workspace), today=lambda: date(2026, 7, 28)
            )
            result = await tool.run(
                {
                    "path": "FastAPI学习.md",
                    "title": "FastAPI学习",
                    "status": "doing",
                    "tags": ["Python", "FastAPI"],
                },
                granted_access=ToolAccess.WORKSPACE_WRITE,
            )

            self.assertEqual(
                note.read_text(encoding="utf-8"),
                "---\ntitle: FastAPI学习\nstatus: doing\ncreated: 2026-07-28\n"
                "tags:\n  - Python\n  - FastAPI\n---\n\n# FastAPI学习\n\n正文\n",
            )
            self.assertEqual(
                json.loads(result.content),
                {"path": "FastAPI学习.md", "created": "2026-07-28"},
            )

    async def test_quotes_yaml_and_refuses_existing_frontmatter_or_unsafe_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            quoted = workspace / "quoted.md"
            quoted.write_text("body\n", encoding="utf-8")
            existing = workspace / "existing.md"
            original = "---\ntitle: Existing\n---\n\nbody\n"
            existing.write_text(original, encoding="utf-8")
            tool = CreateFrontmatterTool(
                _settings(workspace), today=lambda: date(2026, 7, 28)
            )
            await tool.run(
                {
                    "path": "quoted.md",
                    "title": "API: Fast #1",
                    "status": "todo",
                    "tags": ["on"],
                },
                granted_access=ToolAccess.WORKSPACE_WRITE,
            )
            self.assertIn(
                'title: "API: Fast #1"\nstatus: todo\ncreated: 2026-07-28\ntags:\n  - "on"',
                quoted.read_text(encoding="utf-8"),
            )

            invalid_arguments = (
                {"path": "existing.md", "title": "x", "status": "todo"},
                {"path": "missing.md", "title": "x", "status": "todo"},
                {"path": "../outside.md", "title": "x", "status": "todo"},
                {"path": ".obsidian/x.md", "title": "x", "status": "todo"},
                {"path": "x.txt", "title": "x", "status": "todo"},
                {"path": "quoted.md", "title": "x", "status": "todo", "content": "x"},
            )
            for arguments in invalid_arguments:
                with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                    await tool.run(arguments, granted_access=ToolAccess.WORKSPACE_WRITE)
            self.assertEqual(existing.read_text(encoding="utf-8"), original)

    async def test_permissions_prevent_writes_in_read_only_and_plan_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            note = workspace / "blocked.md"
            note.write_text("body\n", encoding="utf-8")
            invocation = ToolInvocation(
                "call-1",
                "create_frontmatter",
                {"path": "blocked.md", "title": "blocked", "status": "todo"},
            )
            read_only = create_session(
                _settings(workspace, SandboxMode.READ_ONLY),
                AgentHandle(),
                client=object(),
            )
            read_only_result = await read_only.tool_runtime.execute(invocation, 1)
            writable = create_session(
                _settings(workspace), AgentHandle(), client=object()
            )
            plan_result = await writable.tool_runtime.execute(
                ToolInvocation("call-2", "create_frontmatter", dict(invocation.arguments)),
                2,
                ModeKind.PLAN,
            )

            self.assertTrue(read_only_result.is_error)
            self.assertTrue(plan_result.is_error)
            self.assertEqual(note.read_text(encoding="utf-8"), "body\n")

    async def test_frontmatter_change_is_recorded_and_can_be_undone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "vault"
            workspace.mkdir()
            note = workspace / "note.md"
            note.write_text("body\n", encoding="utf-8")
            journal = ChangeJournal(workspace, Path(directory) / "sessions.db")
            session = create_session(
                _settings(workspace),
                AgentHandle(),
                client=object(),
                session_id="session",
                change_journal=journal,
            )
            result = await session.tool_runtime.execute(
                ToolInvocation(
                    "call-1",
                    "create_frontmatter",
                    {"path": "note.md", "title": "Note", "status": "doing"},
                ),
                7,
            )
            summary = journal.finish_turn("session", 7)

            self.assertFalse(result.is_error)
            assert summary is not None
            journal.undo("session", str(summary["id"]), ["note.md"])
            self.assertEqual(note.read_text(encoding="utf-8"), "body\n")


def _settings(
    workspace: Path, sandbox_mode: SandboxMode = SandboxMode.WORKSPACE_WRITE
) -> Settings:
    return Settings(
        api_key=None,
        model="test-model",
        base_url="http://unused",
        system_prompt="test",
        workspace=workspace,
        shell_enabled=False,
        request_timeout_seconds=1,
        sandbox_mode=sandbox_mode,
    )


if __name__ == "__main__":
    unittest.main()
