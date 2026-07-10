"""Agent 工具基础协议。"""

from __future__ import annotations

from dataclasses import dataclass
from inspect import isawaitable
from typing import Any, Callable, Mapping, Protocol

from .definitions import ToolDefinition, ToolResult


class Tool(Protocol):
    """暴露给 Agent Core 的可执行能力。"""

    @property
    def definition(self) -> ToolDefinition:
        """返回用于工具选择的静态元数据。"""
        ...

    async def run(self, input_data: Mapping[str, Any], context: Any = None) -> ToolResult:
        """使用已校验的 Agent 输入执行工具。"""
        ...


@dataclass(frozen=True)
class FunctionTool:
    """把同步或异步函数包装成工具。"""

    definition: ToolDefinition
    handler: Callable[[Mapping[str, Any], Any], Any]

    async def run(self, input_data: Mapping[str, Any], context: Any = None) -> ToolResult:
        value = self.handler(input_data, context)
        if isawaitable(value):
            value = await value
        if isinstance(value, ToolResult):
            return value
        return ToolResult(output=value)
