"""模型工具 schema 与内部 ToolInvocation 的转换层。"""

from __future__ import annotations

from typing import Any

from agent.permissions import PermissionRequirement, ToolAccess
from agent.protocol.mode import ModeKind
from agent.tools.invocation import ToolInvocation
from agent.tools.registry import ToolRegistry
from agent.tools.types import ToolExecution


class ToolRouter:
    """阻止模型 API 格式扩散到 Registry 和具体处理器。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def model_visible_specs(self) -> list[dict[str, Any]]:
        """只公布当前真正注册且可执行的工具。"""

        return [handler.spec.as_openai_function() for handler in self._registry.handlers()]

    def build_invocation(
        self, call_id: str, name: str, arguments: dict[str, Any]
    ) -> ToolInvocation:
        """模型调用进入工具系统时只转换一次格式。"""

        return ToolInvocation(call_id=call_id, name=name, arguments=arguments)

    def supports_parallel_tool_calls(self, invocation: ToolInvocation) -> bool:
        """让运行时只依赖路由边界，而不需要了解具体 Handler。"""

        return self._registry.supports_parallel_tool_calls(invocation.name)

    def permission_requirement(
        self, invocation: ToolInvocation, mode: ModeKind = ModeKind.DEFAULT
    ) -> PermissionRequirement | None:
        """返回处理器声明的完整权限需求；未知工具仍由 Registry 报错。"""

        return self._registry.permission_requirement(invocation, mode)

    async def dispatch(
        self,
        invocation: ToolInvocation,
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
        submission_id: int | None = None,
    ) -> ToolExecution:
        """提供运行时所需的单一分发入口。"""

        return await self._registry.dispatch(
            invocation,
            granted_access=granted_access,
            mode=mode,
            submission_id=submission_id,
        )
