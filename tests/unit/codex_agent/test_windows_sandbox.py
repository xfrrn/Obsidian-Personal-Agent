"""项目自带 Windows Restricted Token 后端的黑盒隔离检查。"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent.config.settings import Settings
from agent.core.handle import AgentHandle
from agent.core.loop import create_session
from agent.core.turn.public_events import PublicEventAdapter
from agent.protocol.event import EventKind
from agent.protocol.mode import ModeKind
from agent.tools.invocation import ToolInvocation
from agent.sandbox.windows import (
    _delete_appcontainer_profile,
    _local_sid,
    _set_path_ace,
    spawn,
)
from agent.permissions import SandboxMode
from agent.sandbox import SandboxNetwork


@unittest.skipUnless(os.name == "nt", "Windows native sandbox only")
class WindowsSandboxTest(unittest.IsolatedAsyncioTestCase):
    async def test_plan_mode_overrides_full_access_with_real_read_only_sandbox(self) -> None:
        with _sandbox_tree() as (_, workspace, state):
            source = workspace / "plan-source.txt"
            source.write_text("plan-readable", encoding="utf-8")
            session = create_session(
                _settings(
                    workspace,
                    state,
                    mode=SandboxMode.DANGER_FULL_ACCESS,
                ),
                AgentHandle(),
                client=object(),
            )
            try:
                result = await session.tool_runtime.execute(
                    ToolInvocation(
                        "call-plan-real",
                        "exec_command",
                        {
                            "command": (
                                "type plan-source.txt "
                                "& echo denied>plan-write.txt"
                            )
                        },
                    ),
                    submission_id=1,
                    mode=ModeKind.PLAN,
                )
            finally:
                session.close()

            self.assertFalse(result.is_error, result.content)
            self.assertIn("plan-readable", result.content)
            self.assertFalse((workspace / "plan-write.txt").exists())

    async def test_tool_runtime_reaches_native_sandbox_with_child_process(self) -> None:
        with _sandbox_tree() as (root, workspace, state):
            git_dir = workspace / ".git"
            git_dir.mkdir()
            config = git_dir / "config"
            config.write_text("original", encoding="utf-8")
            session = create_session(
                _settings(workspace, state), AgentHandle(), client=object()
            )
            try:
                child = await session.tool_runtime.execute(
                    ToolInvocation(
                        "call-child",
                        "exec_command",
                        {"command": 'cmd.exe /d /q /c "echo child>child.txt"'},
                    ),
                    submission_id=1,
                )
                escape = await session.tool_runtime.execute(
                    ToolInvocation(
                        "call-escape",
                        "exec_command",
                        {
                            "command": (
                                'echo escaped>"..\\outside.txt" '
                                '& echo changed>".git\\config"'
                            )
                        },
                    ),
                    submission_id=1,
                )
                with patch.dict(os.environ, {"OPENAI_API_KEY": "do-not-leak"}):
                    secret = await session.tool_runtime.execute(
                        ToolInvocation(
                            "call-secret",
                            "exec_command",
                            {
                                "command": (
                                    "if defined OPENAI_API_KEY "
                                    "(echo leaked>secret.txt) "
                                    "else (echo stripped>secret.txt)"
                                )
                            },
                        ),
                        submission_id=1,
                    )
            finally:
                session.close()

            self.assertFalse(child.is_error)
            self.assertEqual(json.loads(child.content)["exit_code"], 0)
            self.assertEqual(
                (workspace / "child.txt").read_text(encoding="utf-8").strip(),
                "child",
            )
            self.assertFalse(escape.is_error)
            self.assertFalse((root / "outside.txt").exists())
            self.assertEqual(config.read_text(encoding="utf-8"), "original")
            self.assertFalse(secret.is_error)
            self.assertEqual(
                (workspace / "secret.txt").read_text(encoding="utf-8").strip(),
                "stripped",
            )

    async def test_tool_runtime_read_only_rejects_a_real_write(self) -> None:
        with _sandbox_tree() as (_, workspace, state):
            source = workspace / "source.txt"
            source.write_text("readable", encoding="utf-8")
            session = create_session(
                _settings(workspace, state, mode=SandboxMode.READ_ONLY),
                AgentHandle(),
                client=object(),
            )
            try:
                result = await session.tool_runtime.execute(
                    ToolInvocation(
                        "call-read-only",
                        "exec_command",
                        {"command": "type source.txt & echo denied>read-only.txt"},
                    ),
                    submission_id=2,
                )
            finally:
                session.close()

            self.assertFalse(result.is_error)
            self.assertIn("readable", result.content)
            self.assertFalse((workspace / "read-only.txt").exists())

    async def test_real_host_execution_waits_for_one_matching_approval(self) -> None:
        with _sandbox_tree() as (root, workspace, state):
            handle = AgentHandle()
            events = PublicEventAdapter()
            handle.turn_events.subscribe(events)
            session = create_session(
                _settings(workspace, state), handle, client=object()
            )
            marker = root / "host-approved.txt"
            task = asyncio.create_task(
                session.tool_runtime.execute(
                    ToolInvocation(
                        "call-host",
                        "exec_command",
                        {
                            "command": f'echo approved>"{marker}"',
                            "sandbox_permissions": "require_escalated",
                            "justification": "端到端验证一次性宿主执行",
                        },
                    ),
                    submission_id=3,
                )
            )
            try:
                event = await asyncio.wait_for(events.receive(), timeout=2)
                self.assertIs(event.kind, EventKind.APPROVAL_REQUESTED)
                self.assertFalse(marker.exists())
                self.assertFalse(
                    session.tool_runtime.resolve_approval("call-host", True, 4)
                )
                self.assertTrue(
                    session.tool_runtime.resolve_approval("call-host", True, 3)
                )
                result = await asyncio.wait_for(task, timeout=5)
            finally:
                if not task.done():
                    task.cancel()
                session.close()

            self.assertFalse(result.is_error)
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), "approved")
            self.assertFalse(
                session.tool_runtime.resolve_approval("call-host", True, 3)
            )

    async def test_tool_runtime_cancellation_reaps_a_real_child_tree(self) -> None:
        with _sandbox_tree() as (_, workspace, state):
            session = create_session(
                _settings(workspace, state, mode=SandboxMode.READ_ONLY),
                AgentHandle(),
                client=object(),
            )
            task = asyncio.create_task(
                session.tool_runtime.execute(
                    ToolInvocation(
                        "call-cancel",
                        "exec_command",
                        {
                            "command": (
                                'cmd.exe /d /q /c "for /L %i in (1,0,2) do @rem"'
                            )
                        },
                    ),
                    submission_id=4,
                )
            )
            try:
                await asyncio.sleep(0.1)
                self.assertTrue(session.tool_runtime.cancel("call-cancel"))
                result = await asyncio.wait_for(task, timeout=3)
            finally:
                if not task.done():
                    task.cancel()
                session.close()

            self.assertTrue(result.is_error)
            self.assertTrue(result.interrupted)

    async def test_workspace_write_cannot_escape_or_modify_git_metadata(self) -> None:
        with _sandbox_tree() as (root, workspace, state):
            git_dir = workspace / ".git"
            git_dir.mkdir()
            config = git_dir / "config"
            config.write_text("original", encoding="utf-8")

            process = await spawn(
                'echo inside>inside.txt & echo outside>"..\\outside.txt" '
                '& echo changed>".git\\config"',
                cwd=workspace,
                state_dir=state,
                mode=SandboxMode.WORKSPACE_WRITE,
                environment=dict(os.environ),
            )
            await process.communicate()

            self.assertTrue((workspace / "inside.txt").is_file())
            self.assertFalse((root / "outside.txt").exists())
            self.assertEqual(config.read_text(encoding="utf-8"), "original")

    async def test_appcontainer_blocks_a_world_writable_escape(self) -> None:
        with _sandbox_tree() as (root, workspace, state):
            world_writable = root / "world-writable"
            world_writable.mkdir()
            with _local_sid("S-1-1-0") as everyone:
                _set_path_ace(world_writable, everyone, deny=False)

            process = await spawn(
                'echo escaped>"..\\world-writable\\escaped.txt"',
                cwd=workspace,
                state_dir=state,
                mode=SandboxMode.WORKSPACE_WRITE,
                environment=dict(os.environ),
                network=SandboxNetwork.BLOCKED,
            )
            await process.communicate()

            self.assertFalse((world_writable / "escaped.txt").exists())

    async def test_read_only_uses_a_distinct_non_writable_capability(self) -> None:
        with _sandbox_tree() as (_, workspace, state):
            first = await spawn(
                "echo allowed>workspace-write.txt",
                cwd=workspace,
                state_dir=state,
                mode=SandboxMode.WORKSPACE_WRITE,
                environment=dict(os.environ),
            )
            await first.communicate()
            self.assertTrue((workspace / "workspace-write.txt").exists())

            second = await spawn(
                "type workspace-write.txt & echo denied>read-only.txt",
                cwd=workspace,
                state_dir=state,
                mode=SandboxMode.READ_ONLY,
                environment=dict(os.environ),
                network=SandboxNetwork.BLOCKED,
            )
            output, _ = await second.communicate()

            self.assertIn(b"allowed", output)
            self.assertFalse((workspace / "read-only.txt").exists())

    async def test_job_object_terminates_the_process_tree(self) -> None:
        with _sandbox_tree() as (_, workspace, state):
            process = await spawn(
                'cmd.exe /d /q /c "for /L %i in (1,0,2) do @rem"',
                cwd=workspace,
                state_dir=state,
                mode=SandboxMode.READ_ONLY,
                environment=dict(os.environ),
                network=SandboxNetwork.BLOCKED,
            )
            collecting = asyncio.create_task(process.communicate())
            await asyncio.sleep(0.1)
            self.assertFalse(collecting.done())
            process.kill()
            await asyncio.wait_for(collecting, timeout=2)

        self.assertIsNotNone(process.returncode)

    async def test_process_stdin_is_writable(self) -> None:
        with _sandbox_tree() as (_, workspace, state):
            process = await spawn(
                'cmd.exe /d /v:on /c "set /p VALUE= & echo got:!VALUE!"',
                cwd=workspace,
                state_dir=state,
                mode=SandboxMode.READ_ONLY,
                environment=dict(os.environ),
                network=SandboxNetwork.BLOCKED,
            )
            collecting = asyncio.create_task(process.communicate())
            await process.write(b"hello\r\n")
            output, _ = await asyncio.wait_for(collecting, timeout=2)

        self.assertIn(b"got:hello", output)
        self.assertEqual(process.returncode, 0)

    async def test_appcontainer_blocks_loopback_network(self) -> None:
        connected = asyncio.Event()

        async def accept(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            connected.set()
            writer.close()

        server = await asyncio.start_server(accept, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with _sandbox_tree() as (_, workspace, state):
                process = await spawn(
                    "curl.exe --version & "
                    f'curl.exe --noproxy "*" --connect-timeout 1 --max-time 1 '
                    f"http://127.0.0.1:{port}/",
                    cwd=workspace,
                    state_dir=state,
                    mode=SandboxMode.READ_ONLY,
                    environment=dict(os.environ),
                    network=SandboxNetwork.BLOCKED,
                )
                output, _ = await process.communicate()
        finally:
            server.close()
            await server.wait_closed()

        self.assertNotEqual(process.returncode, 0)
        # 先证明外部 curl 子进程能够启动，避免把“程序没有运行”误判为网络阻断。
        self.assertIn(b"curl ", output)
        self.assertFalse(connected.is_set())


class _sandbox_tree:
    def __init__(self) -> None:
        # 在仓库 ACL 下创建临时根，避免 Windows 用户 Temp 的 owner-only ACL 掩盖沙盒行为。
        self._temporary = tempfile.TemporaryDirectory(
            prefix=".native-sandbox-test-", dir=Path.cwd()
        )
        self.workspace: Path | None = None

    def __enter__(self) -> tuple[Path, Path, Path]:
        root = Path(self._temporary.name)
        workspace = root / "workspace"
        workspace.mkdir()
        self.workspace = workspace
        return root, workspace, root / "state"

    def __exit__(self, *_: object) -> None:
        if self.workspace is not None:
            for mode in (SandboxMode.READ_ONLY, SandboxMode.WORKSPACE_WRITE):
                _delete_appcontainer_profile(self.workspace, mode)
        self._temporary.cleanup()


def _settings(
    workspace: Path,
    state: Path,
    *,
    mode: SandboxMode = SandboxMode.WORKSPACE_WRITE,
) -> Settings:
    return Settings(
        api_key=None,
        model="test-model",
        base_url="http://unused",
        system_prompt="test",
        workspace=workspace,
        shell_enabled=True,
        request_timeout_seconds=5,
        sandbox_mode=mode,
        sandbox_network=SandboxNetwork.BLOCKED,
        sandbox_state_dir=state,
    )


if __name__ == "__main__":
    unittest.main()
