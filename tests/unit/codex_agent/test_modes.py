"""Default/Plan mode 的提示、权限、持久化和真实工具边界。"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from agent.config.loader import build_mode_system_prompt
from agent.config.settings import Settings
from agent.core.handle import AgentHandle
from agent.core.loop import create_session, start_agent
from agent.core.turn.public_events import PublicEventAdapter
from agent.llm.types import AssistantResponse
from agent.permissions import (
    PermissionDecisionKind,
    PermissionPolicy,
    PermissionRequirement,
    SandboxMode,
    ToolAccess,
)
from agent.protocol.event import EventKind
from agent.protocol.mode import ModeKind
from agent.protocol.op import UserInput
from agent.sandbox import SandboxBackend
from agent.storage import SessionStore
from agent.tools.invocation import ToolInvocation


class _CompletedProcess:
    def __init__(self) -> None:
        self._output = b"ok"

    async def read(self, size: int = 65_536) -> bytes:
        output, self._output = self._output, b""
        return output

    async def write(self, data: bytes) -> None:
        return None

    async def wait(self) -> int:
        return 0

    def kill(self) -> None:
        return None


class _CaptureClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AssistantResponse:
        self.messages = messages
        return AssistantResponse("计划已生成")


class ModeTest(unittest.IsolatedAsyncioTestCase):
    def test_plan_prompt_and_permission_policy_are_explicit(self) -> None:
        prompt = build_mode_system_prompt("base", ModeKind.PLAN)
        policy = PermissionPolicy(SandboxMode.DANGER_FULL_ACCESS)

        self.assertIn("# Plan Mode", prompt)
        self.assertIn("禁止修改文件", prompt)
        self.assertIs(
            policy.decide(
                PermissionRequirement(ToolAccess.WORKSPACE_WRITE), ModeKind.PLAN
            ).kind,
            PermissionDecisionKind.DENY,
        )
        self.assertIs(
            policy.decide(
                PermissionRequirement(ToolAccess.HOST_EXECUTION), ModeKind.PLAN
            ).kind,
            PermissionDecisionKind.DENY,
        )
        self.assertIs(
            policy.decide(
                PermissionRequirement(ToolAccess.OS_SANDBOX_EXECUTION),
                ModeKind.PLAN,
            ).kind,
            PermissionDecisionKind.ALLOW,
        )

    async def test_plan_mode_is_frozen_into_turn_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = _settings(root)
            store = SessionStore(settings.session_db_path)
            stored = store.create(settings.model, root)
            client = _CaptureClient()
            handle, runner = await start_agent(
                settings,
                client,
                session_id=stored.id,
                store=store,
            )
            events = PublicEventAdapter()
            handle.turn_events.subscribe(events)
            handle.submit(UserInput("分析实现方案", ModeKind.PLAN))

            started = None
            while True:
                event = await asyncio.wait_for(events.receive(), timeout=2)
                if event.kind is EventKind.TURN_STARTED:
                    started = event
                if event.kind is EventKind.TURN_FINISHED:
                    break

            handle.shutdown()
            await runner
            restored = store.load(stored.id)

        assert started is not None and restored is not None
        self.assertEqual(started.data["mode"], "plan")
        self.assertIn("# Plan Mode", client.messages[0]["content"])
        self.assertIs(restored.mode, ModeKind.PLAN)

    async def test_plan_mode_rejects_apply_patch_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = create_session(_settings(root), AgentHandle(), object())
            result = await session.tool_runtime.execute(
                ToolInvocation(
                    "call-patch",
                    "apply_patch",
                    {"patch": "*** Begin Patch\n*** Add File: blocked.txt\n+no\n*** End Patch"},
                ),
                1,
                ModeKind.PLAN,
            )
            session.close()

            self.assertTrue(result.is_error)
            self.assertIn("Plan Mode", result.content)
            self.assertFalse((root / "blocked.txt").exists())

    async def test_plan_exec_forces_read_only_sandbox(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = _settings(
                root,
                shell_enabled=True,
                sandbox_mode=SandboxMode.DANGER_FULL_ACCESS,
                sandbox_backend=SandboxBackend.NATIVE_WINDOWS,
            )
            spawn = AsyncMock(return_value=_CompletedProcess())
            with (
                patch(
                    "agent.tools.handlers.exec_command._native_windows_available",
                    return_value=True,
                ),
                patch("agent.tools.handlers.exec_command.windows_sandbox.spawn", spawn),
            ):
                session = create_session(settings, AgentHandle(), object())
                result = await session.tool_runtime.execute(
                    ToolInvocation("call-exec", "exec_command", {"command": "echo ok"}),
                    1,
                    ModeKind.PLAN,
                )
                await session.aclose()

            self.assertFalse(result.is_error, result.content)
            self.assertIs(spawn.await_args.kwargs["mode"], SandboxMode.READ_ONLY)


class ModeMigrationTest(unittest.TestCase):
    def test_v1_session_database_migrates_to_default_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.db"
            connection = sqlite3.connect(path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE sessions (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        archived_at INTEGER,
                        model TEXT NOT NULL,
                        workspace TEXT NOT NULL,
                        context_window_number INTEGER NOT NULL DEFAULT 0,
                        context_summary TEXT,
                        active_from_seq INTEGER NOT NULL DEFAULT 1,
                        last_turn_state TEXT NOT NULL DEFAULT 'idle'
                    );
                    INSERT INTO sessions (
                        id, title, created_at, updated_at, model, workspace
                    ) VALUES ('old', '旧会话', 1, 1, 'test', '.');
                    PRAGMA user_version = 1;
                    """
                )
                connection.commit()
            finally:
                # sqlite3 的 context manager 只提交事务；Windows 上必须显式关闭文件句柄。
                connection.close()

            restored = SessionStore(path).load("old")

        assert restored is not None
        self.assertIs(restored.mode, ModeKind.DEFAULT)
        self.assertIsNone(restored.current_plan)


def _settings(
    workspace: Path,
    *,
    shell_enabled: bool = False,
    sandbox_mode: SandboxMode = SandboxMode.WORKSPACE_WRITE,
    sandbox_backend: SandboxBackend = SandboxBackend.AUTO,
) -> Settings:
    return Settings(
        api_key=None,
        model="test-model",
        base_url="http://unused",
        system_prompt="base",
        workspace=workspace,
        shell_enabled=shell_enabled,
        request_timeout_seconds=1,
        max_tool_rounds=2,
        sandbox_mode=sandbox_mode,
        sandbox_backend=sandbox_backend,
        session_db_path=workspace / "sessions.db",
    )


if __name__ == "__main__":
    unittest.main()
