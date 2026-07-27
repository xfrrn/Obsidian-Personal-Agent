"""Agent 对外发出的事件定义。协议层保持零业务依赖。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventKind(str, Enum):
    """调用方可据此决定如何展示事件，而不需要解析人类文本。"""

    TURN_STARTED = "turn_started"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    APPROVAL_REQUESTED = "approval_requested"
    TOOL_RESULT = "tool_result"
    PLAN_UPDATED = "plan_updated"
    TURN_FINISHED = "turn_finished"
    TURN_INTERRUPTED = "turn_interrupted"
    ERROR = "error"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class Event:
    """运行时到入口层的单向消息。

    ``text`` 适合直接显示，``data`` 保留给 TUI 或 API 等需要结构化内容的入口。
    两者并存可避免把展示格式耦合进核心循环。
    """

    kind: EventKind
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
