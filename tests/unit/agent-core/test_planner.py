from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "agent-core"))

from conversation import ContextBuilder, ConversationManager  # noqa: E402
from intent.intent_types import IntentResult, IntentType  # noqa: E402
from planner import (  # noqa: E402
    DetectedIntent,
    MultiIntentResult,
    PlannerInput,
    PlannerTrigger,
    PlannerTriggerType,
    PlanExecutor,
    PlanStepType,
    PlanValidator,
    RuleBasedPlanner,
)
from tools import (  # noqa: E402
    ToolDefinition,
    ToolEffect,
    ToolInvocationPolicy,
    ToolPermission,
    ToolPolicy,
    ToolRegistry,
    ToolRiskLevel,
)


def _context(user_input: str = "查询任务"):
    return ContextBuilder(ConversationManager()).build(
        conversation_id="c1",
        user_input=user_input,
        active_file_path="Projects/A.md",
        metadata={"pendingOperationPlanId": "op_1"},
    )


def _tools():
    return (
        ToolDefinition("list_tasks", "查询任务"),
        ToolDefinition("search_notes", "搜索笔记"),
        ToolDefinition("search_projects", "搜索项目"),
        ToolDefinition(
            "build_operation_plan",
            "生成操作计划",
            permission=ToolPermission.WRITE,
            risk_level=ToolRiskLevel.MEDIUM,
            effect=ToolEffect.PREPARE_WRITE,
        ),
        ToolDefinition(
            "execute_operation_plan",
            "执行操作计划",
            permission=ToolPermission.WRITE,
            risk_level=ToolRiskLevel.HIGH,
            effect=ToolEffect.WRITE,
            invocation_policy=ToolInvocationPolicy.SYSTEM_ONLY,
            requires_confirmation=True,
        ),
    )


def _single_intent(intent: IntentType, raw_text: str = "test") -> MultiIntentResult:
    return MultiIntentResult(
        raw_text=raw_text,
        intents=(DetectedIntent("intent_1", intent, 0.9, order=1),),
    )


def test_read_intent_maps_to_read_tool() -> None:
    input_data = PlannerInput(_single_intent(IntentType.TASK_SEARCH), _context(), _tools())
    plan = RuleBasedPlanner().create_plan(input_data)

    assert not plan.contains_write_request
    assert plan.steps[0].type is PlanStepType.TOOL_CALL
    assert plan.steps[0].tool_name == "list_tasks"
    assert PlanValidator().validate(plan, input_data).valid


def test_general_chat_answers_directly() -> None:
    input_data = PlannerInput(_single_intent(IntentType.CHAT_GENERAL, "你好"), _context("你好"), _tools())
    plan = RuleBasedPlanner().create_plan(input_data)

    assert not plan.contains_write_request
    assert plan.steps[0].type is PlanStepType.ANSWER_DIRECTLY
    assert "你好" in plan.steps[0].message


def test_write_intents_are_merged_into_operation_plan() -> None:
    intents = MultiIntentResult(
        raw_text="优化当前笔记并添加标签",
        intents=(
            DetectedIntent("intent_1", IntentType.NOTE_OPTIMIZE, 0.9, order=1),
            DetectedIntent("intent_2", IntentType.NOTE_TAG, 0.9, order=2),
        ),
    )
    input_data = PlannerInput(intents, _context("优化当前笔记并添加标签"), _tools())
    plan = RuleBasedPlanner().create_plan(input_data)

    assert plan.contains_write_request
    assert len(plan.steps) == 1
    assert plan.steps[0].tool_name == "build_operation_plan"
    assert len(plan.steps[0].arguments["requestedOperations"]) == 2
    assert PlanValidator().validate(plan, input_data).valid


def test_confirmed_trigger_can_execute_operation_plan() -> None:
    input_data = PlannerInput(
        _single_intent(IntentType.UNKNOWN),
        _context(),
        _tools(),
        PlannerTrigger(PlannerTriggerType.OPERATION_CONFIRMED, operation_plan_id="op_1"),
    )
    plan = RuleBasedPlanner().create_plan(input_data)

    assert plan.steps[0].tool_name == "execute_operation_plan"
    assert PlanValidator().validate(plan, input_data).valid


def test_user_message_cannot_call_system_write_tool() -> None:
    input_data = PlannerInput(_single_intent(IntentType.TASK_SEARCH), _context(), _tools())
    plan = RuleBasedPlanner()._execute_operation_plan(input_data, "op_1")
    result = PlanValidator().validate(plan, input_data)

    assert not result.valid
    assert result.errors


async def test_plan_executor_calls_tools_and_stops_after_build_plan() -> None:
    registry = ToolRegistry(ToolPolicy.allow({ToolPermission.READ, ToolPermission.WRITE}, ToolRiskLevel.MEDIUM))
    registry.register_function(
        _tools()[3],
        lambda input_data, _context: {"operationPlanId": "op_1", "input": input_data},
    )

    input_data = PlannerInput(_single_intent(IntentType.NOTE_CREATE), _context("创建笔记"), _tools())
    plan = RuleBasedPlanner().create_plan(input_data)
    result = await PlanExecutor(registry).execute(plan)

    assert result.stopped
    assert result.step_results[0].status == "completed"


if __name__ == "__main__":
    test_read_intent_maps_to_read_tool()
    test_general_chat_answers_directly()
    test_write_intents_are_merged_into_operation_plan()
    test_confirmed_trigger_can_execute_operation_plan()
    test_user_message_cannot_call_system_write_tool()
    asyncio.run(test_plan_executor_calls_tools_and_stops_after_build_plan())
