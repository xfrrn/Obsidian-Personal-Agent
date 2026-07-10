"""Agent 计划数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from intent.intent_types import IntentEntity, IntentResult, IntentType


class PlannerTriggerType(str, Enum):
    """触发 Planner 的事件类型。"""

    USER_MESSAGE = "user_message"
    OPERATION_CONFIRMED = "operation_confirmed"
    OPERATION_REJECTED = "operation_rejected"
    TOOL_RESULT = "tool_result"


@dataclass(frozen=True)
class PlannerTrigger:
    """Planner 触发事件。"""

    type: PlannerTriggerType = PlannerTriggerType.USER_MESSAGE
    operation_plan_id: str | None = None
    tool_call_id: str | None = None


@dataclass(frozen=True)
class DetectedIntent:
    """排序后的单个意图。"""

    id: str
    type: IntentType
    confidence: float
    entities: tuple[IntentEntity, ...] = ()
    order: int = 0
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class MultiIntentResult:
    """为多意图规划准备的意图结果。"""

    raw_text: str
    intents: tuple[DetectedIntent, ...]
    requires_clarification: bool = False

    @classmethod
    def from_intent_result(cls, result: IntentResult) -> "MultiIntentResult":
        """把单意图识别结果转换成多意图结构。"""
        return cls(
            raw_text=result.raw_text,
            intents=(
                DetectedIntent(
                    id="intent_1",
                    type=result.intent,
                    confidence=result.confidence,
                    entities=result.entities,
                    order=1,
                ),
            ),
            requires_clarification=result.requires_confirmation,
        )


class PlanStepType(str, Enum):
    """计划步骤类型。"""

    TOOL_CALL = "tool_call"
    ASK_CLARIFICATION = "ask_clarification"
    ANSWER_DIRECTLY = "answer_directly"
    AWAIT_CONFIRMATION = "await_confirmation"


@dataclass(frozen=True)
class PlanStep:
    """AgentPlan 中的一个步骤。"""

    type: PlanStepType
    id: str = field(default_factory=lambda: f"step_{uuid4().hex}")
    depends_on: tuple[str, ...] = ()
    description: str = ""
    tool_name: str | None = None
    arguments: Mapping[str, Any] = field(default_factory=dict)
    message: str = ""
    missing_fields: tuple[str, ...] = ()
    operation_plan_id: str | None = None


@dataclass(frozen=True)
class AgentPlan:
    """Planner 生成的结构化计划。"""

    goal: str
    source_text: str
    steps: tuple[PlanStep, ...]
    contains_write_request: bool
    summary: str
    id: str = field(default_factory=lambda: f"plan_{uuid4().hex}")
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
