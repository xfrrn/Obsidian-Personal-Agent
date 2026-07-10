"""薄 AgentPlan 执行器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tools.definitions import ToolCall
from tools.registry import ToolRegistry

from .plan import AgentPlan, PlanStep, PlanStepType


class PlanStepStatus(str):
    """计划步骤执行状态。"""

    COMPLETED = "completed"
    FAILED = "failed"
    WAITING_USER = "waiting_user"
    WAITING_CONFIRMATION = "waiting_confirmation"


@dataclass(frozen=True)
class PlanStepExecutionResult:
    """单个计划步骤执行结果。"""

    step_id: str
    status: str
    output: Any = None


@dataclass(frozen=True)
class PlanExecutionResult:
    """计划执行结果。"""

    plan_id: str
    step_results: tuple[PlanStepExecutionResult, ...] = field(default_factory=tuple)
    stopped: bool = False


class PlanExecutor:
    """只负责按计划调用工具，不负责安全决策。"""

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self._tool_registry = tool_registry

    async def execute(self, plan: AgentPlan, *, context: Any = None) -> PlanExecutionResult:
        """顺序执行计划步骤。"""
        results: list[PlanStepExecutionResult] = []

        for step in plan.steps:
            if step.type is PlanStepType.ASK_CLARIFICATION:
                return self._stopped(plan, results, step, PlanStepStatus.WAITING_USER, {"message": step.message})

            if step.type is PlanStepType.ANSWER_DIRECTLY:
                results.append(
                    PlanStepExecutionResult(
                        step.id,
                        PlanStepStatus.COMPLETED,
                        {"message": step.message},
                    )
                )
                continue

            if step.type is PlanStepType.AWAIT_CONFIRMATION:
                return self._stopped(
                    plan,
                    results,
                    step,
                    PlanStepStatus.WAITING_CONFIRMATION,
                    {"operationPlanId": step.operation_plan_id, "message": step.message},
                )

            if not step.tool_name:
                return self._stopped(plan, results, step, PlanStepStatus.FAILED, {"error": "tool_name is required"})

            try:
                output = await self._tool_registry.run(
                    ToolCall(step.tool_name, step.arguments, step.id),
                    context=context,
                    confirmed=step.tool_name == "execute_operation_plan",
                )
            except Exception as exc:
                return self._stopped(plan, results, step, PlanStepStatus.FAILED, {"error": str(exc)})

            results.append(PlanStepExecutionResult(step.id, PlanStepStatus.COMPLETED, output))
            if step.tool_name == "build_operation_plan":
                return PlanExecutionResult(plan.id, tuple(results), stopped=True)

        return PlanExecutionResult(plan.id, tuple(results), stopped=False)

    def _stopped(
        self,
        plan: AgentPlan,
        results: list[PlanStepExecutionResult],
        step: PlanStep,
        status: str,
        output: Any,
    ) -> PlanExecutionResult:
        results.append(PlanStepExecutionResult(step.id, status, output))
        return PlanExecutionResult(plan.id, tuple(results), stopped=True)
