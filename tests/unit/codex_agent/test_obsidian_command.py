"""Obsidian CLI 工具的参数、权限和进程边界。"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from agent.config.settings import Settings
from agent.core.handle import AgentHandle
from agent.core.loop import create_session
from agent.core.turn.public_events import PublicEventAdapter
from agent.permissions import SandboxMode, ToolAccess
from agent.protocol.event import EventKind
from agent.protocol.mode import ModeKind
from agent.tools.handlers.obsidian_command import ObsidianCommandTool
from agent.tools.invocation import ToolInvocation
from agent.tools.processes import MAX_OUTPUT_BYTES


class CompletedProcess:
    def __init__(self, output: bytes = b"ok", exit_code: int = 0) -> None:
        self.output = output
        self.exit_code = exit_code
        self.returncode: int | None = None
        self.killed = False

    async def communicate(self) -> tuple[bytes, None]:
        self.returncode = self.exit_code
        return self.output, None

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else self.exit_code


class HangingProcess(CompletedProcess):
    async def communicate(self) -> tuple[bytes, None]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class ObsidianCommandToolTest(unittest.IsolatedAsyncioTestCase):
    def test_schema_and_argument_validation(self) -> None:
        tool = ObsidianCommandTool(_settings(Path.cwd()))

        self.assertEqual(tool.spec.name, "obsidian_command")
        self.assertIs(tool.required_access_for({"action": "list"}), ToolAccess.READ_ONLY)
        self.assertIs(
            tool.required_access_for({"action": "execute", "command_id": "x:y"}),
            ToolAccess.HOST_EXECUTION,
        )
        for arguments in (
            {},
            {"action": "unknown"},
            {"action": "execute"},
            {"action": "execute", "command_id": "x", "filter": "x"},
            {"action": "list", "command_id": "x"},
            {"action": "list", "extra": "x"},
        ):
            with self.assertRaises(ValueError):
                tool.required_access_for(arguments)

    async def test_list_uses_fixed_arguments_workspace_and_sanitized_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            tool = ObsidianCommandTool(_settings(workspace))
            process = CompletedProcess(b"obsidian-linter:lint-file\n")
            spawn = AsyncMock(return_value=process)
            with patch(
                "agent.tools.handlers.obsidian_command.shutil.which",
                return_value="C:/Obsidian/Obsidian.com",
            ), patch(
                "agent.tools.handlers.obsidian_command.asyncio.create_subprocess_exec",
                new=spawn,
            ), patch.dict(os.environ, {"OPENAI_API_KEY": "do-not-leak"}):
                result = await tool.run({"action": "list", "filter": "obsidian-linter"})

        self.assertFalse(result.is_error)
        self.assertEqual(
            spawn.await_args.args,
            ("C:/Obsidian/Obsidian.com", "commands", "filter=obsidian-linter"),
        )
        self.assertEqual(spawn.await_args.kwargs["cwd"], str(workspace))
        self.assertNotIn("OPENAI_API_KEY", spawn.await_args.kwargs["env"])
        payload = json.loads(result.content)
        self.assertEqual(payload["exit_code"], 0)
        self.assertIn("lint-file", payload["output"])

    async def test_list_refreshes_persisted_windows_path_without_agent_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tool = ObsidianCommandTool(_settings(Path(directory)))
            spawn = AsyncMock(return_value=CompletedProcess())
            which = Mock(side_effect=[None, "D:/Apps/Obsidian/Obsidian.com"])
            with patch(
                "agent.tools.handlers.obsidian_command.os.name", "nt"
            ), patch(
                "agent.tools.handlers.obsidian_command._windows_registered_path",
                return_value="D:/Apps/Obsidian",
            ), patch(
                "agent.tools.handlers.obsidian_command.shutil.which", new=which
            ), patch(
                "agent.tools.handlers.obsidian_command.asyncio.create_subprocess_exec",
                new=spawn,
            ):
                result = await tool.run({"action": "list"})

        self.assertFalse(result.is_error)
        self.assertEqual(which.call_args_list[1].kwargs["path"], "D:/Apps/Obsidian")
        self.assertEqual(spawn.await_args.args[0], "D:/Apps/Obsidian/Obsidian.com")

    async def test_execute_waits_for_one_time_host_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            handle = AgentHandle()
            session = create_session(_settings(Path(directory)), handle, client=object())
            events = PublicEventAdapter()
            handle.turn_events.subscribe(events)
            process = CompletedProcess()
            spawn = AsyncMock(return_value=process)
            with patch(
                "agent.tools.handlers.obsidian_command.shutil.which",
                return_value="obsidian",
            ), patch(
                "agent.tools.handlers.obsidian_command.asyncio.create_subprocess_exec",
                new=spawn,
            ):
                task = asyncio.create_task(
                    session.tool_runtime.execute(
                        ToolInvocation(
                            "call-1",
                            "obsidian_command",
                            {"action": "execute", "command_id": "obsidian-linter:lint-file"},
                        ),
                        submission_id=7,
                    )
                )
                event = await asyncio.wait_for(events.receive(), timeout=1)
                self.assertIs(event.kind, EventKind.APPROVAL_REQUESTED)
                self.assertEqual(event.data["command"], "obsidian command id=obsidian-linter:lint-file")
                spawn.assert_not_awaited()
                self.assertTrue(session.tool_runtime.resolve_approval("call-1", True, 7))
                result = await asyncio.wait_for(task, timeout=1)

        self.assertFalse(result.is_error)
        spawn.assert_awaited_once()

    async def test_denied_and_plan_mode_execution_never_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            handle = AgentHandle()
            session = create_session(_settings(Path(directory)), handle, client=object())
            events = PublicEventAdapter()
            handle.turn_events.subscribe(events)
            spawn = AsyncMock(return_value=CompletedProcess())
            invocation = ToolInvocation(
                "call-1",
                "obsidian_command",
                {"action": "execute", "command_id": "plugin:command"},
            )
            with patch(
                "agent.tools.handlers.obsidian_command.shutil.which", return_value="obsidian"
            ), patch(
                "agent.tools.handlers.obsidian_command.asyncio.create_subprocess_exec",
                new=spawn,
            ):
                denied = asyncio.create_task(
                    session.tool_runtime.execute(invocation, submission_id=7)
                )
                await asyncio.wait_for(events.receive(), timeout=1)
                self.assertTrue(session.tool_runtime.resolve_approval("call-1", False, 7))
                denied_result = await asyncio.wait_for(denied, timeout=1)
                plan_result = await session.tool_runtime.execute(
                    ToolInvocation(
                        "call-2",
                        "obsidian_command",
                        {"action": "execute", "command_id": "plugin:command"},
                    ),
                    submission_id=8,
                    mode=ModeKind.PLAN,
                )

        self.assertTrue(denied_result.is_error)
        self.assertIn("用户拒绝", denied_result.content)
        self.assertTrue(plan_result.is_error)
        self.assertIn("Plan Mode", plan_result.content)
        spawn.assert_not_awaited()

    async def test_tool_is_registered_when_shell_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = create_session(
                _settings(Path(directory), shell_enabled=False),
                AgentHandle(),
                client=object(),
            )

        names = {handler.spec.name for handler in session.tools.handlers()}
        self.assertIn("obsidian_command", names)
        self.assertNotIn("exec_command", names)

    async def test_missing_cli_nonzero_timeout_and_output_limit_are_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            tool = ObsidianCommandTool(_settings(workspace, timeout=0.01))
            with patch(
                "agent.tools.handlers.obsidian_command.shutil.which", return_value=None
            ):
                missing = await tool.run({"action": "list"})

            nonzero_process = CompletedProcess(b"failed", exit_code=2)
            with patch(
                "agent.tools.handlers.obsidian_command.shutil.which", return_value="obsidian"
            ), patch(
                "agent.tools.handlers.obsidian_command.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=nonzero_process),
            ):
                nonzero = await tool.run({"action": "list"})

            hanging_process = HangingProcess()
            with patch(
                "agent.tools.handlers.obsidian_command.shutil.which", return_value="obsidian"
            ), patch(
                "agent.tools.handlers.obsidian_command.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=hanging_process),
            ):
                timeout = await tool.run({"action": "list"})

            large_process = CompletedProcess(b"x" * (MAX_OUTPUT_BYTES + 100))
            with patch(
                "agent.tools.handlers.obsidian_command.shutil.which", return_value="obsidian"
            ), patch(
                "agent.tools.handlers.obsidian_command.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=large_process),
            ):
                large = await tool.run({"action": "list"})

        self.assertTrue(missing.is_error)
        self.assertIn("1.12.7+", missing.content)
        self.assertTrue(nonzero.is_error)
        self.assertEqual(json.loads(nonzero.content)["exit_code"], 2)
        self.assertTrue(timeout.is_error)
        self.assertTrue(hanging_process.killed)
        self.assertEqual(json.loads(large.content)["omitted_bytes"], 100)


def _settings(
    workspace: Path,
    *,
    shell_enabled: bool = False,
    timeout: float = 1,
) -> Settings:
    return Settings(
        api_key=None,
        model="test-model",
        base_url="http://unused",
        system_prompt="test",
        workspace=workspace,
        shell_enabled=shell_enabled,
        request_timeout_seconds=timeout,
        sandbox_mode=SandboxMode.WORKSPACE_WRITE,
    )


if __name__ == "__main__":
    unittest.main()
