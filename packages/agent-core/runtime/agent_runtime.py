"""Agent Runtime 协调器。"""

from __future__ import annotations

from dataclasses import dataclass
from inspect import isawaitable
from typing import Any

from conversation import ContextBuilder, ConversationManager
from conversation.conversation_manager import ConversationRole
from intent import RuleBasedIntentClassifier
from intent.intent_types import IntentResult, IntentType
from planner import (
    AgentPlan,
    MultiIntentResult,
    PlanExecutionResult,
    PlanValidator,
    PlannerInput,
    PlannerTrigger,
    PlannerTriggerType,
    PlanValidationResult,
    RuleBasedPlanner,
)
from tools import ToolRegistry

from .cancellation import CancellationToken
from .execution_context import RuntimeRequest, RuntimeTrigger


@dataclass(frozen=True)
class AgentRunResult:
    """一次 Agent Runtime 运行结果。"""

    request: RuntimeRequest
    intent: IntentResult
    plan: AgentPlan
    validation: PlanValidationResult
    execution: PlanExecutionResult | None
    assistant_message: str


class AgentRuntime:
    """串联意图识别、上下文、规划、校验和工具执行。"""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        intent_classifier: Any | None = None,
        conversations: ConversationManager | None = None,
        context_builder: ContextBuilder | None = None,
        planner: RuleBasedPlanner | None = None,
        validator: PlanValidator | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._intent_classifier = intent_classifier or RuleBasedIntentClassifier()
        self._conversations = conversations or ConversationManager()
        self._context_builder = context_builder or ContextBuilder(self._conversations)
        self._planner = planner or RuleBasedPlanner()
        self._validator = validator or PlanValidator()

    async def run(
        self,
        request: RuntimeRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AgentRunResult:
        """执行一次 Agent 回合。"""
        token = cancellation or CancellationToken()
        token.throw_if_cancelled()

        self._conversations.start_conversation(request.conversation_id)
        if request.trigger is RuntimeTrigger.USER_MESSAGE:
            self._conversations.append_user_message(request.conversation_id, request.user_input)

        intent = await self._classify(request)
        token.throw_if_cancelled()

        context = self._context_builder.build(
            conversation_id=request.conversation_id,
            user_input=request.user_input,
            scope=request.scope,
            intent=intent,
            available_tools=self._tool_registry.definitions(),
            active_file_path=request.active_file_path,
            selected_text=request.selected_text,
            metadata=request.metadata,
        )
        planner_input = PlannerInput(
            intent_result=MultiIntentResult.from_intent_result(intent),
            context=context,
            tools=self._tool_registry.definitions(),
            trigger=self._planner_trigger(request),
        )
        plan = self._planner.create_plan(planner_input)
        validation = self._validator.validate(plan, planner_input)
        if not validation.valid:
            message = "计划校验失败：" + "；".join(validation.errors)
            self._conversations.append_assistant_message(request.conversation_id, message)
            return AgentRunResult(request, intent, plan, validation, None, message)

        token.throw_if_cancelled()
        from planner import PlanExecutor

        execution = await PlanExecutor(self._tool_registry).execute(plan, context=context)
        message = self._message_from_execution(execution)
        self._conversations.append(
            request.conversation_id,
            ConversationRole.ASSISTANT,
            message,
            {"planId": plan.id},
        )
        return AgentRunResult(request, intent, plan, validation, execution, message)

    async def _classify(self, request: RuntimeRequest) -> IntentResult:
        if request.trigger is RuntimeTrigger.OPERATION_CONFIRMED:
            return self._system_intent(request.user_input or "operation confirmed")
        if request.trigger is RuntimeTrigger.OPERATION_REJECTED:
            return self._system_intent(request.user_input or "operation rejected")

        value = self._intent_classifier.classify(request.user_input)
        if isawaitable(value):
            value = await value
        return value

    def _system_intent(self, raw_text: str) -> IntentResult:
        return IntentResult(
            intent=IntentType.UNKNOWN,
            confidence=1,
            entities=(),
            raw_text=raw_text,
            requires_confirmation=False,
            candidates=(),
        )

    def _planner_trigger(self, request: RuntimeRequest) -> PlannerTrigger:
        if request.trigger is RuntimeTrigger.OPERATION_CONFIRMED:
            return PlannerTrigger(
                PlannerTriggerType.OPERATION_CONFIRMED,
                operation_plan_id=request.operation_plan_id,
            )
        if request.trigger is RuntimeTrigger.OPERATION_REJECTED:
            return PlannerTrigger(
                PlannerTriggerType.OPERATION_REJECTED,
                operation_plan_id=request.operation_plan_id,
            )
        return PlannerTrigger(PlannerTriggerType.USER_MESSAGE)

    def _message_from_execution(self, execution: PlanExecutionResult) -> str:
        if not execution.step_results:
            return "没有执行任何步骤。"
        last = execution.step_results[-1]
        output = last.output
        if isinstance(output, dict) and isinstance(output.get("message"), str):
            return output["message"]
        if isinstance(output, dict) and isinstance(output.get("error"), str):
            return output["error"]
        return "已完成计划。" if not execution.stopped else "计划已暂停，等待下一步。"
