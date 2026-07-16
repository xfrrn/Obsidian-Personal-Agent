"""Codex-style Agent runtime loop."""

from __future__ import annotations

from dataclasses import dataclass
from inspect import isawaitable
import json
from typing import Any, Callable, Mapping
from uuid import uuid4

from conversation import ContextBuilder, ConversationManager
from conversation.conversation_manager import ConversationRole
from exceptions import ToolPermissionDeniedError
from intent import RuleBasedIntentClassifier
from intent.intent_types import IntentResult, IntentType
from planner import AgentPlan, PlanExecutionResult, PlanStepExecutionResult, PlanValidationResult
from planner.planner import INTENT_TOOL_RULES
from tools import ToolRegistry, ToolResult
from tools.definitions import ToolCall, ToolDefinition

from .cancellation import CancellationToken
from .execution_context import RuntimeRequest

AgentClient = Callable[[list[dict[str, Any]], tuple[Any, ...]], Any]
TraceCallback = Callable[["AgentTraceStep"], Any]

_SAFE_READ_TOOL_NAMES = frozenset({"read_note", "search_notes"})
_INTENT_TOOL_NAMES = {
    rule.intent: rule.tool_name
    for rule in INTENT_TOOL_RULES
    if rule.tool_name
}


@dataclass(frozen=True)
class AgentRunResult:
    """Result of one Agent runtime run."""

    request: RuntimeRequest
    intent: IntentResult
    plan: AgentPlan
    validation: PlanValidationResult
    execution: PlanExecutionResult | None
    assistant_message: str
    trace: tuple["AgentTraceStep", ...] = ()


@dataclass(frozen=True)
class AgentToolRequest:
    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class AgentDecision:
    final_answer: str = ""
    tool_calls: tuple[AgentToolRequest, ...] = ()
    content: str = ""


@dataclass(frozen=True)
class AgentTraceStep:
    round: int
    tool_name: str
    status: str
    summary: str
    detail: Mapping[str, Any]


class AgentRuntime:
    """Run one observe-act loop driven by an LLM tool-calling client."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        conversations: ConversationManager | None = None,
        context_builder: ContextBuilder | None = None,
        agent_client: AgentClient | None = None,
        max_agent_rounds: int = 5,
    ) -> None:
        self._tool_registry = tool_registry
        self._conversations = conversations or ConversationManager()
        self._context_builder = context_builder or ContextBuilder(self._conversations)
        self._agent_client = agent_client
        self._max_agent_rounds = max(1, max_agent_rounds)
        self._intent_classifier = RuleBasedIntentClassifier()

    async def run(
        self,
        request: RuntimeRequest,
        *,
        cancellation: CancellationToken | None = None,
        on_trace: TraceCallback | None = None,
    ) -> AgentRunResult:
        """Run one Agent turn."""
        if self._agent_client is None:
            raise RuntimeError("agent LLM is not configured")

        token = cancellation or CancellationToken()
        token.throw_if_cancelled()

        self._conversations.start_conversation(request.conversation_id)
        self._conversations.append_user_message(request.conversation_id, request.user_input)
        return await self._run_agent_loop(request, token, on_trace)

    async def _run_agent_loop(
        self,
        request: RuntimeRequest,
        token: CancellationToken,
        on_trace: TraceCallback | None,
    ) -> AgentRunResult:
        intent = self._intent_classifier.classify(request.user_input)
        available_tools = _available_tools(intent, self._tool_registry.definitions())
        allowed_tool_names = {tool.name for tool in available_tools}
        context = self._context_builder.build(
            conversation_id=request.conversation_id,
            user_input=request.user_input,
            scope=request.scope,
            intent=intent,
            available_tools=available_tools,
            active_file_path=request.active_file_path,
            selected_text=request.selected_text,
            metadata=request.metadata,
        )
        plan = AgentPlan(
            goal=request.user_input,
            source_text=request.user_input,
            steps=(),
            contains_write_request=False,
            summary="LLM agent loop",
        )
        messages = self._agent_messages(context)
        step_results: list[PlanStepExecutionResult] = []
        trace: list[AgentTraceStep] = []
        final_answer = ""
        reached_limit = True

        for round_no in range(1, self._max_agent_rounds + 1):
            token.throw_if_cancelled()
            decision = await self._agent_decision(messages, available_tools)
            if decision.final_answer.strip():
                final_answer = decision.final_answer.strip()
                reached_limit = False
                break
            if not decision.tool_calls:
                if decision.content.startswith("Tool call parse error:"):
                    messages.append({"role": "system", "content": decision.content})
                    continue
                final_answer = decision.content.strip() or "我还不能确定下一步，请补充更具体的目标。"
                reached_limit = False
                break

            messages.append(_assistant_tool_message(decision))
            for call in decision.tool_calls:
                try:
                    if call.name not in allowed_tool_names:
                        raise ToolPermissionDeniedError(
                            f"tool not available for intent {intent.intent.value}: {call.name}"
                        )
                    result = await self._tool_registry.run(
                        ToolCall(call.name, call.arguments, call.id),
                        context=context,
                        confirmed=False,
                    )
                    payload = result.output if isinstance(result, ToolResult) else result
                    status = "completed"
                    output: Any = result
                except Exception as exc:
                    payload = {"error": str(exc)}
                    status = "failed"
                    output = payload

                step_results.append(PlanStepExecutionResult(call.id, status, output))
                trace_step = AgentTraceStep(
                    round_no,
                    call.name,
                    status,
                    _trace_summary(payload),
                    _trace_detail(call.arguments, payload),
                )
                trace.append(trace_step)
                await _emit_trace(on_trace, trace_step)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": _json_text(payload),
                })
                if call.name == "build_operation_plan" and status == "completed":
                    final_answer = _message_from_tool_payload(payload)
                    reached_limit = False
                    break
            if final_answer:
                break

        if not final_answer:
            final_answer = "已达到本次最大思考轮数，请缩小问题或继续追问。"

        execution = PlanExecutionResult(plan.id, tuple(step_results), stopped=reached_limit)
        validation = PlanValidationResult(True)
        self._conversations.append(
            request.conversation_id,
            ConversationRole.ASSISTANT,
            final_answer,
            {"planId": plan.id},
        )
        return AgentRunResult(request, intent, plan, validation, execution, final_answer, tuple(trace))

    def _agent_messages(self, context: Any) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{
            "role": "system",
            "content": (
                "You are a local-first Obsidian personal knowledge agent. "
                "Use tools when notes, tasks, rules, or operation plans are needed. "
                "When metadata.referencedPaths is present, read those notes before answering or planning. "
                "Return a final answer when enough information is available. "
                "Never call system-only write execution tools from a user message; create an OperationPlan instead."
            ),
        }]
        for message in context.recent_messages:
            if message.role in {ConversationRole.USER, ConversationRole.ASSISTANT, ConversationRole.SYSTEM}:
                messages.append({"role": message.role.value, "content": message.content})
        if context.active_file_path or context.selected_text or context.metadata:
            messages.append({
                "role": "system",
                "content": _json_text({
                    "scope": context.scope,
                    "activeFilePath": context.active_file_path,
                    "selectedText": context.selected_text,
                    "metadata": context.metadata,
                }),
            })
        return messages

    async def _agent_decision(
        self,
        messages: list[dict[str, Any]],
        tools: tuple[ToolDefinition, ...],
    ) -> AgentDecision:
        assert self._agent_client is not None
        value = self._agent_client(messages, tools)
        if isawaitable(value):
            value = await value
        return _agent_decision(value)


def _available_tools(
    intent: IntentResult,
    definitions: tuple[ToolDefinition, ...],
) -> tuple[ToolDefinition, ...]:
    primary = _INTENT_TOOL_NAMES.get(intent.intent)
    allowed = {primary} if primary else set()
    if intent.intent is IntentType.UNKNOWN or primary in {"search_notes", "build_operation_plan"}:
        allowed.update(_SAFE_READ_TOOL_NAMES)
    if intent.intent is IntentType.NOTE_INSPECT:
        allowed.update((*_SAFE_READ_TOOL_NAMES, "list_rules"))
    return tuple(tool for tool in definitions if tool.name in allowed)


def _message_from_tool_payload(payload: Any) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("message"), str):
        return payload["message"]
    if isinstance(payload, dict) and isinstance(payload.get("error"), str):
        return payload["error"]
    return "已完成计划。"


def _trace_summary(payload: Any) -> str:
    if isinstance(payload, dict):
        if isinstance(payload.get("message"), str):
            return payload["message"][:240]
        if isinstance(payload.get("error"), str):
            return payload["error"][:240]
        for key in ("count", "noteCount", "taskCount", "issueCount"):
            if key in payload:
                return f"{key}: {payload[key]}"
    return _json_text(payload)[:240]


def _trace_detail(arguments: Mapping[str, Any], payload: Any) -> Mapping[str, Any]:
    detail: dict[str, Any] = {}
    if arguments:
        detail["input"] = _compact_value(arguments)
    if isinstance(payload, dict):
        if isinstance(payload.get("results"), (list, tuple)):
            detail["results"] = _compact_items(payload["results"])
        if isinstance(payload.get("notes"), (list, tuple)):
            detail["notes"] = _compact_items(payload["notes"])
        if isinstance(payload.get("tasks"), (list, tuple)):
            detail["tasks"] = _compact_items(payload["tasks"])
        for key in ("path", "query", "project", "operationPlanId"):
            if key in payload:
                detail[key] = _compact_value(payload[key])
    return detail


def _compact_items(items: Any, limit: int = 5) -> list[Any]:
    if not isinstance(items, (list, tuple)):
        return []
    return [_compact_value(item) for item in items[:limit]]


def _compact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key in ("path", "title", "score", "line", "heading", "project", "status", "count", "query"):
            if key in value:
                result[key] = _compact_value(value[key])
        return result or {str(key): _compact_value(item) for key, item in list(value.items())[:6]}
    if isinstance(value, (list, tuple)):
        return [_compact_value(item) for item in value[:5]]
    if isinstance(value, str):
        return value[:160]
    return value


async def _emit_trace(callback: TraceCallback | None, step: AgentTraceStep) -> None:
    if callback is None:
        return
    value = callback(step)
    if isawaitable(value):
        await value


def _agent_decision(value: Any) -> AgentDecision:
    if isinstance(value, AgentDecision):
        return value
    if not isinstance(value, Mapping):
        return AgentDecision(final_answer=str(value))

    final = value.get("final_answer") or value.get("finalAnswer")
    calls = value.get("tool_calls") or value.get("toolCalls") or ()
    tool_calls: list[AgentToolRequest] = []
    errors: list[str] = []
    for item in calls:
        if not isinstance(item, Mapping):
            continue
        try:
            tool_calls.append(_tool_request(item))
        except Exception as exc:
            errors.append(str(exc))

    if errors and not tool_calls:
        return AgentDecision(content="Tool call parse error: " + "; ".join(errors))
    return AgentDecision(
        final_answer=final if isinstance(final, str) else "",
        content=str(value.get("content") or "") if value.get("content") is not None else "",
        tool_calls=tuple(tool_calls),
    )


def _tool_request(item: Mapping[str, Any]) -> AgentToolRequest:
    function = item.get("function") if isinstance(item.get("function"), Mapping) else {}
    name = item.get("name") or item.get("tool_name") or item.get("toolName") or function.get("name")
    arguments = item.get("arguments") if "arguments" in item else function.get("arguments", {})
    if isinstance(arguments, str):
        arguments = json.loads(arguments or "{}")
    if not isinstance(arguments, Mapping):
        raise ValueError("tool arguments must be an object")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("tool name is required")
    return AgentToolRequest(
        id=str(item.get("id") or f"call_{uuid4().hex}"),
        name=name.strip(),
        arguments=arguments,
    )


def _assistant_tool_message(decision: AgentDecision) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": decision.content or None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": _json_text(call.arguments)},
            }
            for call in decision.tool_calls
        ],
    }


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
