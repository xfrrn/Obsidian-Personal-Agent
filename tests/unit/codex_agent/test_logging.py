"""Focused checks for structured, correlated diagnostics."""

from __future__ import annotations

import asyncio
from io import StringIO
import json
import logging
import os
import unittest
from unittest.mock import patch

from agent.core.turn.audit import audit_events
from agent.core.turn.bus import TurnEventBus
from agent.core.turn.events import (
    AssistantDelta,
    ImplicitSkillInvocation,
    ToolRequested,
    ToolResult,
    ToolResultStatus,
    TurnStarted,
)
from agent.tools.invocation import ToolInvocation
from agent.utils.logging import configure_logging, log_context


class _TtyBuffer(StringIO):
    def isatty(self) -> bool:
        return True


class LoggingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._logger = logging.getLogger("agent")
        self._handlers = self._logger.handlers[:]
        self._level = self._logger.level
        self._propagate = self._logger.propagate

    def tearDown(self) -> None:
        for handler in list(self._logger.handlers):
            if handler not in self._handlers:
                self._logger.removeHandler(handler)
                handler.close()
        self._logger.setLevel(self._level)
        self._logger.propagate = self._propagate

    def test_json_output_is_correlated_and_only_emits_allowlisted_fields(self) -> None:
        output = StringIO()
        configure_logging("info", "json", stream=output)
        configure_logging("INFO", "json", stream=output)

        with log_context(submission_id=7, tool_name="exec_command", tool_call_id="call-1"):
            logging.getLogger("agent.core.agent_loop").info(
                "tool.completed",
                extra={"duration_ms": 12, "is_error": False, "tool_arguments": "must-not-appear"},
            )

        entries = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["event"], "tool.completed")
        self.assertEqual(entries[0]["submission_id"], 7)
        self.assertEqual(entries[0]["tool_name"], "exec_command")
        self.assertNotIn("tool_arguments", entries[0])

    def test_text_output_uses_readable_terminal_colors(self) -> None:
        output = _TtyBuffer()
        with patch.dict(os.environ, {"NO_COLOR": ""}):
            configure_logging("INFO", "text", stream=output)

        with log_context(submission_id=7):
            logging.getLogger("agent.core.agent_loop").warning(
                "tool.failed", extra={"duration_ms": 12, "is_error": True}
            )

        line = output.getvalue()
        self.assertIn("\033[", line)
        self.assertIn("WARNING", line)
        self.assertIn("tool.failed", line)
        self.assertIn("submission_id", line)


class AuditLoggingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._logger = logging.getLogger("agent")
        self._handlers = self._logger.handlers[:]
        self._level = self._logger.level
        self._propagate = self._logger.propagate

    def tearDown(self) -> None:
        for handler in list(self._logger.handlers):
            if handler not in self._handlers:
                self._logger.removeHandler(handler)
                handler.close()
        self._logger.setLevel(self._level)
        self._logger.propagate = self._propagate

    async def test_audit_watcher_logs_metadata_without_event_contents(self) -> None:
        output = StringIO()
        configure_logging("INFO", "json", stream=output)
        bus = TurnEventBus()
        task = asyncio.create_task(audit_events(bus.watch()))
        invocation = ToolInvocation("call-1", "exec_command", {"command": "secret-command"})

        bus.emit(TurnStarted(7, "secret-user-text"))
        bus.emit(AssistantDelta(7, "secret-model-text"))
        bus.emit(ToolRequested(7, invocation))
        bus.emit(
            ToolResult(
                7, "call-1", "exec_command", "secret-output", ToolResultStatus.SUCCESS
            )
        )
        bus.emit(ImplicitSkillInvocation(7, "call-1", "safe-skill-name"))
        bus.close()
        await asyncio.wait_for(task, timeout=1)

        lines = output.getvalue()
        entries = [json.loads(line) for line in lines.splitlines()]
        self.assertEqual(
            [entry["event"] for entry in entries],
            [
                "audit.turn_started",
                "audit.assistant_delta",
                "audit.tool_requested",
                "audit.tool_result",
                "audit.skill_invocation",
            ],
        )
        self.assertEqual(entries[1]["delta_chars"], len("secret-model-text"))
        self.assertEqual(entries[-2]["tool_status"], "success")
        self.assertEqual(entries[-1]["skill_name"], "safe-skill-name")
        self.assertNotIn("secret", lines)


if __name__ == "__main__":
    unittest.main()
