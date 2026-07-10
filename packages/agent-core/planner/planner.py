"""规则型 Agent Planner。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol

from conversation.context_builder import AgentTurnContext
from intent.intent_types import IntentEntityType, IntentType
from tools.definitions import ToolDefinition

from .plan import (
    AgentPlan,
    DetectedIntent,
    MultiIntentResult,
    PlannerTrigger,
    PlannerTriggerType,
    PlanStep,
    PlanStepType,
)


class PlannerMode(str, Enum):
    """意图规划模式。"""

    READ = "read"
    PREPARE_WRITE = "prepare_write"
    CLARIFICATION = "clarification"
    DIRECT_ANSWER = "direct_answer"


@dataclass(frozen=True)
class PlannerInput:
    """Planner 输入。"""

    intent_result: MultiIntentResult
    context: AgentTurnContext
    tools: tuple[ToolDefinition, ...]
    trigger: PlannerTrigger = PlannerTrigger()


@dataclass(frozen=True)
class IntentToolRule:
    """意图到工具的静态映射。"""

    intent: IntentType
    mode: PlannerMode
    tool_name: str | None = None


@dataclass(frozen=True)
class ResolvedArguments:
    """补齐后的工具参数。"""

    arguments: Mapping[str, Any]
    missing_fields: tuple[str, ...] = ()


class ArgumentResolver(Protocol):
    """从意图实体和上下文补齐工具参数。"""

    def resolve(self, intent: DetectedIntent, context: AgentTurnContext) -> ResolvedArguments:
        """返回工具参数和缺失字段。"""
        ...


class DefaultArgumentResolver:
    """第一版参数补齐器。"""

    def resolve(self, intent: DetectedIntent, context: AgentTurnContext) -> ResolvedArguments:
        """根据实体和上下文生成工具参数。"""
        arguments: dict[str, Any] = {
            "intent": intent.type.value,
            "rawText": context.user_input,
            "entities": [
                {"type": entity.type.value, "value": entity.value}
                for entity in intent.entities
            ],
        }

        if task_name := self._entity(intent, IntentEntityType.TASK_NAME):
            arguments["taskName"] = task_name
        if project_name := self._entity(intent, IntentEntityType.PROJECT_NAME):
            arguments["projectName"] = project_name
        if keyword := self._entity(intent, IntentEntityType.KEYWORD):
            arguments["keyword"] = keyword
        if folder := self._entity(intent, IntentEntityType.FOLDER):
            arguments["folder"] = folder
        if note_name := self._entity(intent, IntentEntityType.NOTE_NAME):
            arguments["noteName"] = note_name
        if context.active_file_path:
            arguments["activeFilePath"] = context.active_file_path
        if context.selected_text:
            arguments["selectedText"] = context.selected_text
        if current_project := context.metadata.get("currentProject"):
            arguments.setdefault("projectName", current_project)

        missing = self._missing_fields(intent.type, arguments)
        return ResolvedArguments(arguments, missing)

    def _entity(self, intent: DetectedIntent, entity_type: IntentEntityType) -> str | None:
        for entity in intent.entities:
            if entity.type is entity_type:
                return entity.value
        return None

    def _missing_fields(self, intent: IntentType, arguments: Mapping[str, Any]) -> tuple[str, ...]:
        if intent is IntentType.TASK_COMPLETE and not arguments.get("taskName"):
            return ("taskName",)
        if intent in {IntentType.NOTE_ARCHIVE, IntentType.NOTE_OPTIMIZE, IntentType.NOTE_TAG}:
            if not (arguments.get("activeFilePath") or arguments.get("noteName")):
                return ("notePath",)
        return ()


INTENT_TOOL_RULES: tuple[IntentToolRule, ...] = (
    IntentToolRule(IntentType.TASK_SEARCH, PlannerMode.READ, "list_tasks"),
    IntentToolRule(IntentType.NOTE_SEARCH, PlannerMode.READ, "search_notes"),
    IntentToolRule(IntentType.VAULT_SEARCH, PlannerMode.READ, "search_notes"),
    IntentToolRule(IntentType.PROJECT_SEARCH, PlannerMode.READ, "search_projects"),
    IntentToolRule(IntentType.NOTE_CREATE, PlannerMode.PREPARE_WRITE, "build_operation_plan"),
    IntentToolRule(IntentType.NOTE_UPDATE, PlannerMode.PREPARE_WRITE, "build_operation_plan"),
    IntentToolRule(IntentType.NOTE_ARCHIVE, PlannerMode.PREPARE_WRITE, "build_operation_plan"),
    IntentToolRule(IntentType.NOTE_TAG, PlannerMode.PREPARE_WRITE, "build_operation_plan"),
    IntentToolRule(IntentType.NOTE_OPTIMIZE, PlannerMode.PREPARE_WRITE, "build_operation_plan"),
    IntentToolRule(IntentType.TASK_CREATE, PlannerMode.PREPARE_WRITE, "build_operation_plan"),
    IntentToolRule(IntentType.TASK_UPDATE, PlannerMode.PREPARE_WRITE, "build_operation_plan"),
    IntentToolRule(IntentType.TASK_COMPLETE, PlannerMode.PREPARE_WRITE, "build_operation_plan"),
    IntentToolRule(IntentType.TASK_DELETE, PlannerMode.PREPARE_WRITE, "build_operation_plan"),
    IntentToolRule(IntentType.VAULT_ORGANIZE, PlannerMode.PREPARE_WRITE, "build_operation_plan"),
    IntentToolRule(IntentType.PROJECT_ORGANIZE, PlannerMode.PREPARE_WRITE, "build_operation_plan"),
    IntentToolRule(IntentType.CHAT_GENERAL, PlannerMode.DIRECT_ANSWER),
    IntentToolRule(IntentType.UNKNOWN, PlannerMode.CLARIFICATION),
)


class Planner(Protocol):
    """Planner 接口。"""

    def create_plan(self, input_data: PlannerInput) -> AgentPlan:
        """创建 AgentPlan。"""
        ...


class RuleBasedPlanner:
    """按静态规则把意图映射成工具计划。"""

    def __init__(
        self,
        argument_resolver: ArgumentResolver | None = None,
        rules: tuple[IntentToolRule, ...] = INTENT_TOOL_RULES,
    ) -> None:
        self._argument_resolver = argument_resolver or DefaultArgumentResolver()
        self._rules = {rule.intent: rule for rule in rules}

    def create_plan(self, input_data: PlannerInput) -> AgentPlan:
        """创建结构化计划。"""
        trigger = input_data.trigger
        if trigger.type is PlannerTriggerType.OPERATION_CONFIRMED:
            return self._execute_operation_plan(input_data, trigger.operation_plan_id)
        if trigger.type is PlannerTriggerType.OPERATION_REJECTED:
            return self._operation_rejected(input_data)
        if input_data.intent_result.requires_clarification or not input_data.intent_result.intents:
            return self._clarification(input_data, "我还不能确定你希望执行什么操作，请补充具体目标。")

        read_steps: list[PlanStep] = []
        write_requests: list[Mapping[str, Any]] = []
        contains_write = False

        for intent in sorted(input_data.intent_result.intents, key=lambda item: item.order):
            rule = self._rules.get(intent.type)
            if not rule:
                return self._clarification(input_data, f"暂时不支持该操作：{intent.type.value}")
            if rule.mode is PlannerMode.CLARIFICATION:
                return self._clarification(input_data, "我无法确定你的操作意图，请换一种方式描述。")
            if rule.mode is PlannerMode.DIRECT_ANSWER:
                read_steps.append(
                    PlanStep(
                        PlanStepType.ANSWER_DIRECTLY,
                        message="你好，我在。你可以让我查询笔记、列任务，或生成修改计划。",
                    )
                )
                continue

            resolved = self._argument_resolver.resolve(intent, input_data.context)
            if resolved.missing_fields:
                return self._missing_fields(input_data, resolved.missing_fields)

            if rule.mode is PlannerMode.PREPARE_WRITE:
                contains_write = True
                write_requests.append(
                    {
                        "intent": intent.type.value,
                        "arguments": resolved.arguments,
                    }
                )
                continue

            if rule.tool_name:
                read_steps.append(
                    PlanStep(
                        PlanStepType.TOOL_CALL,
                        tool_name=rule.tool_name,
                        arguments=resolved.arguments,
                        description=f"处理意图 {intent.type.value}",
                    )
                )

        steps = tuple(read_steps)
        if write_requests:
            steps = (
                *steps,
                PlanStep(
                    PlanStepType.TOOL_CALL,
                    tool_name="build_operation_plan",
                    arguments={
                        "requestedOperations": write_requests,
                        "context": {
                            "activeFilePath": input_data.context.active_file_path,
                            "selectedText": input_data.context.selected_text,
                            "scope": input_data.context.scope,
                        },
                    },
                    description="为数据修改请求生成 OperationPlan",
                ),
            )

        return AgentPlan(
            goal=input_data.intent_result.raw_text,
            source_text=input_data.intent_result.raw_text,
            steps=steps or (PlanStep(PlanStepType.ASK_CLARIFICATION, message="我还没有生成可执行步骤。"),),
            contains_write_request=contains_write,
            summary="请求包含数据修改，计划先生成 OperationPlan。"
            if contains_write
            else "请求只包含查询或直接回答操作。",
        )

    def _execute_operation_plan(
        self,
        input_data: PlannerInput,
        operation_plan_id: str | None,
    ) -> AgentPlan:
        if not operation_plan_id:
            return self._clarification(input_data, "缺少要执行的 OperationPlan ID。")
        return AgentPlan(
            goal="执行已确认的操作计划",
            source_text=input_data.intent_result.raw_text,
            steps=(
                PlanStep(
                    PlanStepType.TOOL_CALL,
                    tool_name="execute_operation_plan",
                    arguments={"operationPlanId": operation_plan_id},
                    description="执行已经确认的 OperationPlan",
                ),
            ),
            contains_write_request=True,
            summary="收到系统确认事件，准备执行对应的 OperationPlan。",
        )

    def _operation_rejected(self, input_data: PlannerInput) -> AgentPlan:
        return AgentPlan(
            goal="取消操作计划",
            source_text=input_data.intent_result.raw_text,
            steps=(PlanStep(PlanStepType.ANSWER_DIRECTLY, message="已取消本次修改，不会更改任何文件。"),),
            contains_write_request=False,
            summary="用户拒绝了待执行的 OperationPlan。",
        )

    def _clarification(self, input_data: PlannerInput, message: str) -> AgentPlan:
        return AgentPlan(
            goal=input_data.intent_result.raw_text,
            source_text=input_data.intent_result.raw_text,
            steps=(PlanStep(PlanStepType.ASK_CLARIFICATION, message=message),),
            contains_write_request=False,
            summary="当前请求信息不足，需要用户补充。",
        )

    def _missing_fields(self, input_data: PlannerInput, fields: tuple[str, ...]) -> AgentPlan:
        return self._clarification(input_data, f"还缺少必要信息：{'、'.join(fields)}。")
