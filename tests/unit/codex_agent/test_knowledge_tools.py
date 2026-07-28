"""Properties 与 Tasks 语义工具的集中行为检查。"""

from __future__ import annotations

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
from agent.tools.handlers.tasks import MutateTaskTool, QueryTasksTool
from agent.tools.handlers.update_properties import UpdatePropertiesTool
from agent.tools.invocation import ToolInvocation


class KnowledgeToolsTest(unittest.IsolatedAsyncioTestCase):
    async def test_update_properties_preserves_unknown_fields_and_body(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            note = workspace / "project.md"
            note.write_text(
                "---\ntitle: Old\naliases:\n  - Keep\nstatus: todo\n"
                "custom:\n  nested: true\n---\n\n# Body\n",
                encoding="utf-8",
            )
            tool = UpdatePropertiesTool(_settings(workspace))
            result = await tool.run(
                {
                    "path": "project.md",
                    "set": {
                        "status": "doing",
                        "project": "FastAPI",
                        "created": "2026-07-28",
                        "tags": ["Python", "FastAPI"],
                    },
                    "remove": ["title"],
                },
                granted_access=ToolAccess.WORKSPACE_WRITE,
            )

            text = note.read_text(encoding="utf-8")
            self.assertNotIn("title: Old", text)
            self.assertIn("aliases:\n  - Keep\n", text)
            self.assertIn("custom:\n  nested: true\n", text)
            self.assertIn("status: doing\n", text)
            self.assertIn("project: FastAPI\ncreated: 2026-07-28\n", text)
            self.assertIn("tags:\n  - Python\n  - FastAPI\n", text)
            self.assertTrue(text.endswith("---\n\n# Body\n"))
            self.assertEqual(json.loads(result.content)["removed"], ["title"])

            with self.assertRaises(ValueError):
                await tool.run(
                    {"path": "project.md", "set": {"nested": {"x": 1}}},
                    granted_access=ToolAccess.WORKSPACE_WRITE,
                )

    async def test_query_tasks_filters_dates_tags_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "tasks.md").write_text(
                "- [ ] Open #Python 📅 2026-07-30 🔼\n"
                "- [x] Done #Python 📅 2026-07-20\n"
                "- [/] Started #Work 🔁 every week 📅 2026-08-01\n",
                encoding="utf-8",
            )
            hidden = workspace / ".obsidian"
            hidden.mkdir()
            (hidden / "ignored.md").write_text("- [ ] Hidden #Python\n", encoding="utf-8")
            tool = QueryTasksTool(_settings(workspace))

            result = await tool.run(
                {
                    "status": "open",
                    "due_before": "2026-07-31",
                    "tags": ["Python"],
                }
            )
            tasks = json.loads(result.content)["tasks"]

            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["line"], 1)
            self.assertEqual(tasks[0]["priority"], "medium")
            self.assertEqual(tasks[0]["tags"], ["#Python"])

    async def test_mutate_task_creates_and_safely_updates_exact_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            note = workspace / "project.md"
            note.write_text(
                "# Project\n\n## Tasks\n- [ ] Existing\n- [ ] Repeat 🔁 every week\n\n## Notes\nBody\n",
                encoding="utf-8",
            )
            tool = MutateTaskTool(_settings(workspace))
            created = await tool.run(
                {
                    "action": "create",
                    "path": "project.md",
                    "description": "Learn FastAPI",
                    "due": "2026-08-01",
                    "priority": "high",
                    "tags": ["Python"],
                },
                granted_access=ToolAccess.WORKSPACE_WRITE,
            )
            created_payload = json.loads(created.content)
            self.assertEqual(
                created_payload["text"],
                "- [ ] Learn FastAPI #Python ⏫ 📅 2026-08-01",
            )
            self.assertLess(
                note.read_text(encoding="utf-8").index(created_payload["text"]),
                note.read_text(encoding="utf-8").index("## Notes"),
            )

            completed = await tool.run(
                {
                    "action": "complete",
                    "path": "project.md",
                    "line": 4,
                    "expected_text": "- [ ] Existing",
                },
                granted_access=ToolAccess.WORKSPACE_WRITE,
            )
            self.assertEqual(json.loads(completed.content)["text"], "- [x] Existing")
            with self.assertRaisesRegex(ValueError, "原文已经变化"):
                await tool.run(
                    {
                        "action": "reopen",
                        "path": "project.md",
                        "line": 4,
                        "expected_text": "- [ ] Existing",
                    },
                    granted_access=ToolAccess.WORKSPACE_WRITE,
                )
            with self.assertRaisesRegex(ValueError, "循环任务"):
                await tool.run(
                    {
                        "action": "complete",
                        "path": "project.md",
                        "line": 5,
                        "expected_text": "- [ ] Repeat 🔁 every week",
                    },
                    granted_access=ToolAccess.WORKSPACE_WRITE,
                )

    async def test_write_tools_follow_plan_permissions_and_change_undo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "vault"
            workspace.mkdir()
            note = workspace / "note.md"
            original = "---\nstatus: todo\n---\n\n- [ ] Task\n"
            note.write_text(original, encoding="utf-8")
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
                    "update_properties",
                    {"path": "note.md", "set": {"status": "doing"}},
                ),
                7,
            )
            summary = journal.finish_turn("session", 7)

            self.assertFalse(result.is_error)
            assert summary is not None
            journal.undo("session", str(summary["id"]), ["note.md"])
            self.assertEqual(note.read_text(encoding="utf-8"), original)

            denied = await session.tool_runtime.execute(
                ToolInvocation(
                    "call-2",
                    "mutate_task",
                    {
                        "action": "complete",
                        "path": "note.md",
                        "line": 5,
                        "expected_text": "- [ ] Task",
                    },
                ),
                8,
                ModeKind.PLAN,
            )
            self.assertTrue(denied.is_error)
            self.assertEqual(note.read_text(encoding="utf-8"), original)


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
