"""Runtime 输入上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping
from uuid import uuid4


class RuntimeTrigger(str, Enum):
    """触发 Runtime 的事件类型。"""

    USER_MESSAGE = "user_message"
    OPERATION_CONFIRMED = "operation_confirmed"
    OPERATION_REJECTED = "operation_rejected"


@dataclass(frozen=True)
class RuntimeRequest:
    """一次 Runtime 调用请求。"""

    user_input: str
    conversation_id: str = field(default_factory=lambda: uuid4().hex)
    request_id: str = field(default_factory=lambda: uuid4().hex)
    scope: str = "vault"
    trigger: RuntimeTrigger = RuntimeTrigger.USER_MESSAGE
    operation_plan_id: str | None = None
    active_file_path: str | None = None
    selected_text: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
