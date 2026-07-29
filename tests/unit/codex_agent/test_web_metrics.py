"""Web 监控聚合的最小回归测试。"""

from __future__ import annotations

import unittest

from agent.core.turn.events import (
    AssistantResponseReceived,
    ImplicitSkillInvocation,
    ToolRequested,
    ToolResult,
    ToolResultStatus,
    TurnFinished,
    TurnStarted,
)
from agent.llm.types import AssistantResponse, TokenUsage
from agent.tools.invocation import ToolInvocation
from agent.web.metrics import AgentMetrics


class AgentMetricsTest(unittest.TestCase):
    def test_snapshot_counts_usage_tools_and_explicit_skills(self) -> None:
        metrics = AgentMetrics()
        metrics.record(TurnStarted(1, "不要记录我", ("code-review",)))
        metrics.record(
            AssistantResponseReceived(
                1,
                AssistantResponse("完成", usage=TokenUsage(11, 7, 18)),
                streamed=True,
            )
        )
        metrics.record(ToolRequested(1, ToolInvocation("call-1", "exec_command", {})))
        metrics.record(ToolResult(1, "call-1", "exec_command", "ok", ToolResultStatus.SUCCESS))
        metrics.record(ToolRequested(1, ToolInvocation("call-2", "apply_patch", {})))
        metrics.record(ToolResult(1, "call-2", "apply_patch", "failed", ToolResultStatus.ERROR))
        metrics.record(ImplicitSkillInvocation(1, "call-1", "code-review"))
        metrics.record(TurnFinished(1))

        snapshot = metrics.snapshot()

        self.assertEqual(snapshot["tokens"]["total"], 18)
        self.assertEqual(snapshot["tools"]["calls"], 2)
        self.assertEqual(snapshot["tools"]["success_rate"], 50.0)
        self.assertEqual(
            snapshot["tools"]["by_name"]["exec_command"]["success_rate"], 100.0
        )
        self.assertEqual(snapshot["tools"]["by_name"]["apply_patch"]["success_rate"], 0.0)
        self.assertEqual(snapshot["skills"]["explicit_invocations"], 1)
        self.assertEqual(snapshot["skills"]["implicit_invocations"], 1)
        self.assertEqual(snapshot["skills"]["by_name"], {"code-review": 2})
        self.assertEqual(snapshot["active_turns"], 0)
        self.assertNotIn("不要记录我", str(snapshot))
