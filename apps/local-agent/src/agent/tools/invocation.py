"""一次工具调用的运行时输入。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """将模型返回的调用 id、工具名和参数绑定为不可变对象。"""

    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCallContext:
    """Only handlers that must correlate with their owning turn receive this."""

    call_id: str
    submission_id: int | None
