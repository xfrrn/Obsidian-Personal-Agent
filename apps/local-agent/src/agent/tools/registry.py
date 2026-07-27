"""已注册工具到处理器的显式映射。"""

from __future__ import annotations

from agent.permissions import PermissionRequirement, ToolAccess
from agent.protocol.mode import ModeKind
from agent.tools.handlers.base import ToolHandler
from agent.tools.invocation import ToolInvocation
from agent.tools.types import ToolExecution, ToolExecutionContext


class ToolRegistry:
    """仅负责查找和调用；模型可见 schema 由 ToolRouter 单独处理。"""

    def __init__(self, handlers: list[ToolHandler] | None = None) -> None:
        self._handlers: dict[str, ToolHandler] = {}
        for handler in handlers or []:
            if handler.spec.name in self._handlers:
                raise ValueError(f"重复注册工具: {handler.spec.name}")
            if not isinstance(getattr(handler, "required_access", None), ToolAccess):
                raise ValueError(f"工具 {handler.spec.name} 必须声明 required_access")
            self._handlers[handler.spec.name] = handler

    def handlers(self) -> tuple[ToolHandler, ...]:
        """返回不可变快照，防止路由层意外修改内部注册表。"""

        return tuple(self._handlers.values())

    def supports_parallel_tool_calls(self, name: str) -> bool:
        """仅允许处理器显式声明的无副作用调用并行执行。"""

        handler = self._handlers.get(name)
        return bool(handler is not None and getattr(handler, "supports_parallel_tool_calls", False))

    def permission_requirement(
        self, invocation: ToolInvocation, mode: ModeKind = ModeKind.DEFAULT
    ) -> PermissionRequirement | None:
        """把处理器的静态声明和调用级提权原因合并为一个权限请求。"""

        handler = self._handlers.get(invocation.name)
        if handler is None:
            return None
        resolver = getattr(handler, "required_access_for", None)
        access = (
            resolver(invocation.arguments, mode=mode)
            if callable(resolver)
            else handler.required_access
        )
        if not isinstance(access, ToolAccess):
            raise ValueError(f"工具 {invocation.name} 返回了无效的 required_access")
        reason_resolver = getattr(handler, "approval_reason", None)
        reason = (
            reason_resolver(invocation.arguments) if callable(reason_resolver) else None
        )
        if reason is not None and not isinstance(reason, str):
            raise ValueError(f"工具 {invocation.name} 返回了无效的 approval_reason")
        return PermissionRequirement(access, reason)

    async def dispatch(
        self,
        invocation: ToolInvocation,
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
        context: ToolExecutionContext | None = None,
    ) -> ToolExecution:
        """执行已注册工具；处理器错误变成结果，Agent 可继续让模型调整。"""

        handler = self._handlers.get(invocation.name)
        if handler is None:
            return ToolExecution(f"未知工具: {invocation.name}", is_error=True)
        try:
            kwargs = {"granted_access": granted_access, "mode": mode}
            if getattr(handler, "accepts_execution_context", False):
                kwargs["context"] = context
            result = await handler.run(invocation.arguments, **kwargs)
            return result if isinstance(result, ToolExecution) else ToolExecution(result)
        except Exception as exc:  # 处理器失败不能让整个会话崩溃。
            return ToolExecution(f"工具 {invocation.name} 执行失败: {exc}", is_error=True)
