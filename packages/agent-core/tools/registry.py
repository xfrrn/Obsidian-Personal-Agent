"""Agent 工具注册表。"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from exceptions import ToolNotFoundError, ToolPermissionDeniedError, ToolRegistrationError

from .base import FunctionTool, Tool
from .definitions import ToolCall, ToolDefinition, ToolInvocationPolicy, ToolResult
from .permissions import ToolPolicy


class ToolRegistry:
    """基于工具名称的注册表和执行入口。"""

    def __init__(self, policy: ToolPolicy | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self._policy = policy or ToolPolicy()

    def register(self, tool: Tool) -> None:
        """按工具定义中的名称注册一个工具。"""
        name = tool.definition.name
        if name in self._tools:
            raise ToolRegistrationError(f"tool already registered: {name}")
        self._tools[name] = tool

    def register_function(
        self,
        definition: ToolDefinition,
        handler: Callable[[Mapping[str, Any], Any], Any],
    ) -> None:
        """把同步或异步函数注册为工具。"""
        self.register(FunctionTool(definition, handler))

    def get(self, name: str) -> Tool:
        """按名称返回已注册工具。"""
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"unknown tool: {name}") from exc

    def definitions(self) -> tuple[ToolDefinition, ...]:
        """返回供 Agent 选择工具使用的工具定义。"""
        return tuple(tool.definition for tool in self._tools.values())

    def names(self) -> tuple[str, ...]:
        """返回已注册的工具名称。"""
        return tuple(self._tools)

    async def run(
        self,
        call: ToolCall,
        *,
        context: Any = None,
        confirmed: bool = False,
    ) -> ToolResult:
        """先检查策略，再执行已注册工具。"""
        tool = self.get(call.tool_name)
        if tool.definition.invocation_policy is ToolInvocationPolicy.SYSTEM_ONLY and not confirmed:
            raise ToolPermissionDeniedError("system-only tool requires a confirmed system trigger")
        if tool.definition.requires_confirmation and not confirmed:
            raise ToolPermissionDeniedError("confirmation required")
        decision = self._policy.check(tool.definition, confirmed=confirmed)
        if not decision.allowed:
            raise ToolPermissionDeniedError(decision.reason)
        return await tool.run(call.input, context)

    def extend(self, tools: Iterable[Tool]) -> None:
        """批量注册工具。"""
        for tool in tools:
            self.register(tool)
