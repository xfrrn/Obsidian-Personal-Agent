from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "agent-core"))

from runtime import AgentRuntime, CancellationToken, RuntimeRequest, RuntimeTrigger  # noqa: E402
from tools import (  # noqa: E402
    ToolDefinition,
    ToolEffect,
    ToolInvocationPolicy,
    ToolPermission,
    ToolPolicy,
    ToolRegistry,
    ToolRiskLevel,
)


def _registry() -> ToolRegistry:
    registry = ToolRegistry(ToolPolicy.allow({ToolPermission.READ, ToolPermission.WRITE}, ToolRiskLevel.HIGH))
    registry.register_function(
        ToolDefinition("list_tasks", "查询任务"),
        lambda _input, _context: {"message": "你有 1 个任务"},
    )
    registry.register_function(
        ToolDefinition(
            "execute_operation_plan",
            "执行操作计划",
            permission=ToolPermission.WRITE,
            risk_level=ToolRiskLevel.HIGH,
            effect=ToolEffect.WRITE,
            invocation_policy=ToolInvocationPolicy.SYSTEM_ONLY,
            requires_confirmation=True,
        ),
        lambda input_data, _context: {"message": f"已执行 {input_data['operationPlanId']}"},
    )
    return registry


async def test_runtime_runs_user_message() -> None:
    result = await AgentRuntime(_registry()).run(
        RuntimeRequest("查询一下本周还有哪些任务", conversation_id="c1")
    )

    assert result.validation.valid
    assert result.execution is not None
    assert result.plan.steps[0].tool_name == "list_tasks"


async def test_runtime_runs_confirmed_operation() -> None:
    result = await AgentRuntime(_registry()).run(
        RuntimeRequest(
            "确认执行",
            conversation_id="c1",
            trigger=RuntimeTrigger.OPERATION_CONFIRMED,
            operation_plan_id="op_1",
            metadata={"pendingOperationPlanId": "op_1"},
        )
    )

    assert result.validation.valid
    assert result.plan.steps[0].tool_name == "execute_operation_plan"


async def test_runtime_honors_cancellation() -> None:
    token = CancellationToken()
    token.cancel()
    try:
        await AgentRuntime(_registry()).run(RuntimeRequest("查询任务"), cancellation=token)
    except RuntimeError:
        pass
    else:
        raise AssertionError("cancelled run should fail")


if __name__ == "__main__":
    asyncio.run(test_runtime_runs_user_message())
    asyncio.run(test_runtime_runs_confirmed_operation())
    asyncio.run(test_runtime_honors_cancellation())
