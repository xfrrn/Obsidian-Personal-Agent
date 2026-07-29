"""Turn loop 内部事件；它们携带运行时状态，不能直接暴露给 UI。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, TypeAlias

from agent.llm.types import AssistantResponse
from agent.protocol.mode import ModeKind
from agent.tools.invocation import ToolInvocation
from agent.tools.types import PlanUpdate


class ToolResultStatus(str, Enum):
    """区分工具报错与被中断，避免模型和观察者把两者混为一谈。"""

    SUCCESS = "success"
    ERROR = "error"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class TurnStarted:
    submission_id: int
    user_text: str
    # 只保留已注入 Skill 的名称，供监控使用；不重复携带 SKILL.md 正文。
    mentioned_skill_names: tuple[str, ...] = ()
    mode: ModeKind = ModeKind.DEFAULT


@dataclass(frozen=True, slots=True)
class AssistantResponseReceived:
    submission_id: int
    response: AssistantResponse
    streamed: bool


@dataclass(frozen=True, slots=True)
class AssistantDelta:
    submission_id: int
    text: str
    channel: Literal["content", "reasoning"] = "content"


@dataclass(frozen=True, slots=True)
class ToolRequested:
    submission_id: int
    invocation: ToolInvocation


@dataclass(frozen=True, slots=True)
class ToolApprovalRequested:
    submission_id: int
    invocation: ToolInvocation
    justification: str


@dataclass(frozen=True, slots=True)
class ToolResult:
    submission_id: int
    call_id: str
    name: str
    content: str
    status: ToolResultStatus


@dataclass(frozen=True, slots=True)
class ImplicitSkillInvocation:
    submission_id: int
    call_id: str
    skill_name: str


@dataclass(frozen=True, slots=True)
class PlanUpdated:
    submission_id: int
    update: PlanUpdate


@dataclass(frozen=True, slots=True)
class TurnFinished:
    submission_id: int


@dataclass(frozen=True, slots=True)
class TurnInterrupted:
    submission_id: int


@dataclass(frozen=True, slots=True)
class TurnError:
    submission_id: int | None
    message: str


@dataclass(frozen=True, slots=True)
class RuntimeShutdown:
    """不绑定某个 turn 的有序运行时关闭通知。"""


TurnEvent: TypeAlias = (
    TurnStarted
    | AssistantResponseReceived
    | AssistantDelta
    | ToolRequested
    | ToolApprovalRequested
    | ToolResult
    | ImplicitSkillInvocation
    | PlanUpdated
    | TurnFinished
    | TurnInterrupted
    | TurnError
    | RuntimeShutdown
)


RELIABLE_EVENT_TYPES = (
    TurnStarted,
    AssistantResponseReceived,
    ToolRequested,
    ToolApprovalRequested,
    ToolResult,
    ImplicitSkillInvocation,
    PlanUpdated,
    TurnFinished,
    TurnInterrupted,
    TurnError,
    RuntimeShutdown,
)


def is_reliable_event(event: TurnEvent) -> bool:
    """只有流式增量可丢；其余事件决定回合状态，必须经过可靠映射。"""

    return isinstance(event, RELIABLE_EVENT_TYPES)
