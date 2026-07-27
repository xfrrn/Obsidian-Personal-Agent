"""持久化自检：完整历史、压缩窗口和中断工具都必须可恢复。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.config.settings import Settings
from agent.core.loop import start_agent
from agent.core.turn.conversation import INTERRUPTED_TOOL_CONTENT
from agent.storage import SessionStore


class SessionStoreTest(unittest.TestCase):
    def test_list_is_stably_paginated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions.db")
            for _ in range(3):
                store.create("test-model", Path(directory))

            all_sessions = store.list(limit=3)
            second_page = store.list(limit=1, offset=1)
            self.assertEqual(second_page, (all_sessions[1],))
            with self.assertRaisesRegex(ValueError, "limit"):
                store.list(limit=0)
            with self.assertRaisesRegex(ValueError, "offset"):
                store.list(offset=-1)

    def test_compaction_keeps_full_history_but_restores_only_active_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions.db")
            session = store.create("test-model", Path(directory))
            store.append_messages(
                session.id,
                1,
                (
                    ({"role": "user", "content": "第一轮"}, True),
                    ({"role": "assistant", "content": "旧回答"}, True),
                    ({"role": "user", "content": "第二轮"}, True),
                    ({"role": "assistant", "content": "新回答"}, True),
                ),
                "idle",
            )
            store.save_compaction(session.id, 1, "第一轮摘要")

            restored = store.load(session.id)

        assert restored is not None
        self.assertEqual(len(restored.messages), 4)
        self.assertEqual(
            [message["content"] for message in restored.active_messages],
            ["第二轮", "新回答"],
        )
        self.assertEqual(restored.context_summary, "第一轮摘要")

    def test_archive_hides_a_session_without_deleting_its_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / "sessions.db")
            session = store.create("test-model", Path(directory))
            store.append_messages(
                session.id,
                1,
                (({"role": "user", "content": "保留我"}, True),),
                "idle",
            )
            store.archive(session.id)

            archived = store.load(session.id, include_archived=True)
            visible_sessions = store.list()
            visible_session = store.load(session.id)

        self.assertEqual(visible_sessions, ())
        self.assertIsNone(visible_session)
        assert archived is not None
        self.assertEqual(archived.messages[0].payload["content"], "保留我")


class SessionRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_resume_repairs_a_pending_tool_call_as_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SessionStore(root / "sessions.db")
            stored = store.create("test-model", root)
            store.append_messages(
                stored.id,
                1,
                (
                    ({"role": "user", "content": "执行命令"}, True),
                    (
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": "shell", "arguments": "{}"},
                                }
                            ],
                        },
                        False,
                    ),
                ),
                "running",
            )

            with self.assertLogs("agent.core.loop", level="WARNING") as logs:
                handle, runner = await start_agent(
                    _settings(root), object(), session_id=stored.id, store=store
                )
            handle.shutdown()
            await runner
            restored = store.load(stored.id)

        assert restored is not None
        self.assertEqual(restored.last_turn_state, "interrupted")
        self.assertEqual(restored.messages[-1].role, "tool")
        self.assertEqual(restored.messages[-1].payload["content"], INTERRUPTED_TOOL_CONTENT)
        self.assertIn("session.recovered_possible_orphan_processes", logs.output[0])


def _settings(workspace: Path) -> Settings:
    return Settings(
        api_key=None,
        model="test-model",
        base_url="http://unused",
        system_prompt="test",
        workspace=workspace,
        shell_enabled=False,
        request_timeout_seconds=1,
        session_db_path=workspace / "sessions.db",
    )
