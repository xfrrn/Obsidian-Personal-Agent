"""Agent 工具共享数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Mapping

JsonSchema = Mapping[str, Any]

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class ToolPermission(str, Enum):
    """工具运行所需的权限类别。"""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    ADMIN = "admin"


class ToolRiskLevel(str, Enum):
    """用于预览和确认策略的工具风险级别。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ToolDefinition:
    """Agent 选择工具前可见的静态元数据。"""

    name: str
    description: str
    input_schema: JsonSchema = field(default_factory=lambda: {"type": "object"})
    output_schema: JsonSchema = field(default_factory=lambda: {"type": "object"})
    permission: ToolPermission = ToolPermission.READ
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        if not _TOOL_NAME.fullmatch(self.name):
            raise ValueError(f"invalid tool name: {self.name!r}")
        if not self.description.strip():
            raise ValueError("tool description is required")
        if self.timeout_seconds <= 0:
            raise ValueError("tool timeout must be positive")


@dataclass(frozen=True)
class ToolCall:
    """Agent 对某个工具的一次具体调用请求。"""

    tool_name: str
    input: Mapping[str, Any] = field(default_factory=dict)
    call_id: str | None = None


@dataclass(frozen=True)
class ToolResult:
    """标准化后的工具执行结果。"""

    output: Any
    citations: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
