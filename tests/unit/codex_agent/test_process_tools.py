"""exec_command/write_stdin 进程交互回归测试。"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from agent.config.settings import Settings
from agent.core.handle import AgentHandle
from agent.core.loop import create_session, start_agent
from agent.core.turn.public_events import PublicEventAdapter
from agent.llm.types import AssistantResponse, ToolCall
from agent.permissions import SandboxMode
from agent.protocol.event import EventKind
from agent.protocol.op import UserInput
from agent.tools.handlers.exec_command import ExecCommandTool
from agent.tools.processes import MAX_OUTPUT_BYTES, ProcessManager


class BlockingProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.was_killed = False

    async def read(self, size: int = 65_536) -> bytes:
        self.started.set()
        await self.stopped.wait()
        return b""

    async def write(self, data: bytes) -> None:
        return None

    async def wait(self) -> int:
        await self.stopped.wait()
        assert self.returncode is not None
        return self.returncode

    def kill(self) -> None:
        self.was_killed = True
        self.returncode = -9
        self.stopped.set()


class CompletedProcess:
    def __init__(self, output: bytes) -> None:
        self.returncode: int | None = None
        self._output = output

    async def read(self, size: int = 65_536) -> bytes:
        output, self._output = self._output, b""
        return output

    async def write(self, data: bytes) -> None:
        return None

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = -9


class StreamingProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.read_started = asyncio.Event()
        self.chunk_read = asyncio.Event()
        self._output: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._stopped = asyncio.Event()

    async def read(self, size: int = 65_536) -> bytes:
        self.read_started.set()
        chunk = await self._output.get()
        if chunk is None:
            return b""
        self.chunk_read.set()
        return chunk

    async def write(self, data: bytes) -> None:
        return None

    async def wait(self) -> int:
        await self._stopped.wait()
        assert self.returncode is not None
        return self.returncode

    def emit(self, output: bytes) -> None:
        self._output.put_nowait(output)

    def finish(self, exit_code: int = 0) -> None:
        self.returncode = exit_code
        self._stopped.set()
        self._output.put_nowait(None)

    def kill(self) -> None:
        self.finish(-9)


class SlowClosingProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.reader_cancelled = asyncio.Event()
        self._stdout_closed = asyncio.Event()

    async def read(self, size: int = 65_536) -> bytes:
        try:
            await self._stdout_closed.wait()
        except asyncio.CancelledError:
            self.reader_cancelled.set()
            raise
        return b""

    async def write(self, data: bytes) -> None:
        return None

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = -9
        self._stdout_closed.set()


class ProcessToolsClient:
    """用真实工具结果驱动下一次模型调用，覆盖完整 Agent 工具回路。"""

    def __init__(self) -> None:
        self.calls = 0
        self.visible_tools: set[str] = set()
        self.final_result: dict[str, object] | None = None

    async def complete(
        self, messages: list[dict[str, object]], tools: list[dict[str, object]]
    ) -> AssistantResponse:
        self.calls += 1
        self.visible_tools = {
            str(spec["function"]["name"])
            for spec in tools
            if isinstance(spec.get("function"), dict)
        }
        if self.calls == 1:
            return AssistantResponse(
                None,
                (
                    ToolCall(
                        "call-exec",
                        "exec_command",
                        {"command": _interactive_command(), "yield_time_ms": 250},
                    ),
                ),
            )
        result = json.loads(str(messages[-1]["content"]))
        if self.calls == 2:
            return AssistantResponse(
                None,
                (
                    ToolCall(
                        "call-write",
                        "write_stdin",
                        {
                            "process_id": result["process_id"],
                            "chars": "quit\n",
                            "yield_time_ms": 1_000,
                        },
                    ),
                ),
            )
        self.final_result = result
        return AssistantResponse("进程交互完成")


class ProcessInteractionTest(unittest.IsolatedAsyncioTestCase):
    async def test_agent_runs_exec_then_write_stdin_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ProcessToolsClient()
            handle, runner = await start_agent(
                _settings(
                    Path(directory),
                    SandboxMode.DANGER_FULL_ACCESS,
                ),
                client,
            )
            events = PublicEventAdapter()
            handle.turn_events.subscribe(events)
            tool_calls: list[str] = []
            try:
                handle.submit(UserInput("启动交互进程并退出"))
                while True:
                    event = await asyncio.wait_for(events.receive(), timeout=10)
                    if event.kind is EventKind.TOOL_CALL:
                        tool_calls.append(event.text)
                    elif event.kind is EventKind.TURN_FINISHED:
                        break
            finally:
                handle.shutdown()
                await runner

        self.assertEqual(tool_calls, ["exec_command", "write_stdin"])
        self.assertEqual(
            client.visible_tools,
            {
                "apply_patch",
                "current_time",
                "exec_command",
                "get_context_remaining",
                "new_context_window",
                "update_plan",
                "write_stdin",
            },
        )
        assert client.final_result is not None
        self.assertIn("echo:quit", str(client.final_result["output"]))
        self.assertIsNone(client.final_result["process_id"])
        self.assertEqual(client.final_result["exit_code"], 0)

    async def test_enabled_session_registers_both_process_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = create_session(
                _settings(Path(directory), SandboxMode.DANGER_FULL_ACCESS),
                AgentHandle(),
                client=object(),
            )
            try:
                names = {
                    spec["function"]["name"]
                    for spec in session.tool_router.model_visible_specs()
                }
            finally:
                await session.aclose()

        self.assertIn("exec_command", names)
        self.assertIn("write_stdin", names)
        self.assertNotIn("shell", names)

    async def test_model_argument_cannot_grant_its_own_host_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = _settings(Path(directory), SandboxMode.WORKSPACE_WRITE)
            manager = ProcessManager(1)
            with patch(
                "agent.tools.handlers.exec_command._native_windows_available",
                return_value=True,
            ):
                tool = ExecCommandTool(settings, manager)
            host_process = AsyncMock()
            with patch(
                "agent.tools.handlers.exec_command.asyncio.create_subprocess_shell",
                new=host_process,
            ):
                with self.assertRaises(PermissionError):
                    await tool.run(
                        {
                            "command": "echo unsafe",
                            "sandbox_permissions": "require_escalated",
                            "justification": "test",
                        }
                    )

        host_process.assert_not_awaited()

    async def test_cancel_before_returning_id_kills_and_reaps_process(self) -> None:
        process = BlockingProcess()
        manager = ProcessManager(5)

        async def spawn() -> BlockingProcess:
            return process

        task = asyncio.create_task(manager.exec_command(spawn, 30_000))
        await process.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(process.was_killed)
        await manager.aclose()

    async def test_output_buffer_is_bounded(self) -> None:
        manager = ProcessManager(5)
        head = b"h" * (MAX_OUTPUT_BYTES // 2)
        tail = b"t" * (MAX_OUTPUT_BYTES // 2)

        async def spawn() -> CompletedProcess:
            return CompletedProcess(head + b"middle-data" + tail)

        result = await manager.exec_command(spawn, 250)

        self.assertTrue(result.output.startswith(head.decode()))
        self.assertTrue(result.output.endswith(tail.decode()))
        self.assertIn("... 11 bytes omitted ...", result.output)
        self.assertEqual(result.omitted_bytes, 11)
        self.assertIsNone(result.process_id)
        self.assertEqual(result.exit_code, 0)

    async def test_new_output_wakes_and_drains_without_returning_early(self) -> None:
        process = StreamingProcess()
        manager = ProcessManager(5)

        async def spawn() -> StreamingProcess:
            return process

        task = asyncio.create_task(manager.exec_command(spawn, 1_000))
        await process.read_started.wait()
        process.emit(b"ready\n")
        await process.chunk_read.wait()
        for _ in range(100):
            entry = manager._entries.get(1)
            if entry is not None and not entry.output:
                break
            await asyncio.sleep(0.001)

        entry = manager._entries[1]
        self.assertEqual(entry.output, bytearray())
        self.assertFalse(task.done())

        process.finish()
        result = await task
        self.assertEqual(result.output, "ready\n")
        self.assertIsNone(result.process_id)

    async def test_process_exit_waits_at_most_50ms_for_stdout_close(self) -> None:
        process = SlowClosingProcess()
        manager = ProcessManager(5)

        async def spawn() -> SlowClosingProcess:
            return process

        result = await asyncio.wait_for(
            manager.exec_command(spawn, 30_000), timeout=1
        )

        self.assertTrue(process.reader_cancelled.is_set())
        self.assertIsNone(result.process_id)
        self.assertEqual(result.exit_code, 0)

    async def test_hard_lifetime_terminates_process(self) -> None:
        process = BlockingProcess()
        manager = ProcessManager(0.01)

        async def spawn() -> BlockingProcess:
            return process

        result = await manager.exec_command(spawn, 250)

        self.assertTrue(process.was_killed)
        self.assertIsNone(result.process_id)
        self.assertIn("已终止", result.error or "")

    async def test_real_process_can_be_written_polled_and_exited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = ProcessManager(5)
            tool = ExecCommandTool(
                _settings(Path(directory), SandboxMode.DANGER_FULL_ACCESS), manager
            )
            try:
                started = json.loads(
                    await tool.run(
                        {"command": _interactive_command(), "yield_time_ms": 250}
                    )
                )
                process_id = started["process_id"]
                self.assertIsInstance(process_id, int)

                echoed = await manager.write_stdin(process_id, "hello\n", 250)
                output = echoed.output
                if "echo:hello" not in output:
                    output += (await manager.write_stdin(process_id, "", 1_000)).output
                self.assertIn("echo:hello", output)
                self.assertEqual(echoed.process_id, process_id)

                finished = await manager.write_stdin(process_id, "quit\n", 1_000)
                self.assertIn("echo:quit", finished.output)
                self.assertIsNone(finished.process_id)
                self.assertEqual(finished.exit_code, 0)
            finally:
                await manager.aclose()


def _interactive_command() -> str:
    code = (
        "import sys; exec(\"for line in sys.stdin:\\n "
        "print('echo:' + line.strip(), flush=True)\\n "
        "if line.strip() == 'quit': break\")"
    )
    arguments = [sys.executable, "-u", "-c", code]
    return (
        subprocess.list2cmdline(arguments)
        if os.name == "nt"
        else shlex.join(arguments)
    )


def _settings(workspace: Path, mode: SandboxMode) -> Settings:
    return Settings(
        api_key=None,
        model="test-model",
        base_url="http://unused",
        system_prompt="test",
        workspace=workspace,
        shell_enabled=True,
        request_timeout_seconds=5,
        sandbox_mode=mode,
    )


if __name__ == "__main__":
    unittest.main()
