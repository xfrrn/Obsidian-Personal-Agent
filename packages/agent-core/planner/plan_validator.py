"""AgentPlan 安全校验器。"""

from __future__ import annotations

from dataclasses import dataclass, field

from intent.intent_types import IntentType
from tools.definitions import ToolEffect, ToolInvocationPolicy

from .plan import AgentPlan, PlannerTriggerType, PlanStepType
from .planner import PlannerInput


@dataclass(frozen=True)
class PlanValidationResult:
    """计划校验结果。"""

    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass
class PlanValidator:
    """校验计划是否满足安全边界。"""

    def validate(self, plan: AgentPlan, input_data: PlannerInput) -> PlanValidationResult:
        """返回计划校验结果。"""
        errors: list[str] = []
        warnings: list[str] = []
        tools = {tool.name: tool for tool in input_data.tools}

        if not plan.steps:
            errors.append("计划中没有任何步骤")

        for step in plan.steps:
            if step.type is not PlanStepType.TOOL_CALL:
                continue
            if not step.tool_name:
                errors.append(f"工具步骤缺少工具名称：{step.id}")
                continue

            tool = tools.get(step.tool_name)
            if not tool:
                errors.append(f"计划调用了不存在的工具：{step.tool_name}")
                continue

            if (
                tool.invocation_policy is ToolInvocationPolicy.SYSTEM_ONLY
                and input_data.trigger.type is PlannerTriggerType.USER_MESSAGE
            ):
                errors.append(f"普通用户消息不能调用系统工具：{tool.name}")

            if (
                tool.effect is ToolEffect.WRITE
                and input_data.trigger.type is not PlannerTriggerType.OPERATION_CONFIRMED
            ):
                errors.append("写操作必须先生成并确认 OperationPlan")

        if (
            input_data.trigger.type is PlannerTriggerType.USER_MESSAGE
            and plan.contains_write_request
            and any(
            intent.type is IntentType.UNKNOWN for intent in input_data.intent_result.intents
            )
        ):
            errors.append("未知意图不能进入写操作流程")

        if input_data.trigger.type is PlannerTriggerType.OPERATION_CONFIRMED:
            pending = input_data.context.metadata.get("pendingOperationPlanId")
            if pending and pending != input_data.trigger.operation_plan_id:
                errors.append("确认的 OperationPlan 与当前待确认计划不匹配")
            if not pending:
                warnings.append("当前上下文没有 pendingOperationPlanId，跳过确认 ID 匹配校验")

        return PlanValidationResult(not errors, tuple(errors), tuple(warnings))
