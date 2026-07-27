"""工具调用运行时边界。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from agent.permissions import (
    PermissionDenied,
    PermissionManager,
    PermissionPolicy,
    PermissionRequest,
)
from agent.protocol.mode import ModeKind
from agent.tools.invocation import ToolInvocation
from agent.tools.router import ToolRouter
from agent.tools.types import ToolExecution, ToolExecutionContext


class _ToolExecutionGate:
    """让显式安全的调用共享执行期，其他调用独占执行期。"""

    def __init__(self) -> None:
        self._exclusive = asyncio.Lock()
        self._parallel_state = asyncio.Lock()
        self._parallel_count = 0
        self._no_parallel_calls = asyncio.Event()
        self._no_parallel_calls.set()

    @asynccontextmanager
    async def hold(self, supports_parallel: bool) -> AsyncIterator[None]:
        if not supports_parallel:
            # 先阻止新的只读调用进入，再等待已经开始的调用全部收尾。
            async with self._exclusive:
                await self._no_parallel_calls.wait()
                yield
            return

        async with self._exclusive:
            async with self._parallel_state:
                self._parallel_count += 1
                self._no_parallel_calls.clear()
        try:
            yield
        finally:
            async with self._parallel_state:
                self._parallel_count -= 1
                if self._parallel_count == 0:
                    self._no_parallel_calls.set()


class ToolCallRuntime:
    """工具任务的创建、追踪和取消边界，避免生命周期细节泄漏到 Agent 循环。"""

    def __init__(
        self,
        router: ToolRouter,
        permission_manager: PermissionManager | PermissionPolicy | None = None,
    ) -> None:
        self._router = router
        # 保留旧的 Policy 注入入口，但立即转换成统一的有状态授权服务。
        self._permissions = (
            permission_manager
            if isinstance(permission_manager, PermissionManager)
            else PermissionManager(permission_manager or PermissionPolicy())
        )
        self._running: dict[str, asyncio.Task[ToolExecution]] = {}
        self._cancel_requested: set[str] = set()
        self._execution_gate = _ToolExecutionGate()

    async def execute(
        self,
        invocation: ToolInvocation,
        submission_id: int | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
        *,
        session_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ToolExecution:
        task = asyncio.create_task(
            self._dispatch_with_gate(invocation, submission_id, mode, session_id, metadata or {}),
            name=f"agent-tool-{invocation.call_id}",
        )
        self._running[invocation.call_id] = task

        try:
            return await task
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            # 中断整个 turn 也会连带取消子任务；此时必须继续传播，不能误变成单工具取消。
            if invocation.call_id in self._cancel_requested and (
                current_task is None or not current_task.cancelling()
            ):
                return ToolExecution("工具调用已取消。", is_error=True, interrupted=True)
            raise
        finally:
            if self._running.get(invocation.call_id) is task:
                del self._running[invocation.call_id]
            self._cancel_requested.discard(invocation.call_id)

    async def _dispatch_with_gate(
        self,
        invocation: ToolInvocation,
        submission_id: int | None,
        mode: ModeKind,
        session_id: str | None,
        metadata: dict[str, object],
    ) -> ToolExecution:
        context = ToolExecutionContext(session_id, submission_id, mode, metadata)
        async with self._execution_gate.hold(
            self._router.supports_parallel_tool_calls(invocation)
        ):
            try:
                requirement = self._router.permission_requirement(invocation, mode)
            except ValueError as exc:
                return ToolExecution(
                    f"工具 {invocation.name} 权限参数无效: {exc}", is_error=True
                )
            if requirement is None:
                return await self._router.dispatch(invocation, mode=mode, context=context)
            request = PermissionRequest(
                submission_id=submission_id,
                call_id=invocation.call_id,
                tool_name=invocation.name,
                arguments=invocation.arguments,
                requirement=requirement,
                mode=mode,
            )
            try:
                grant = await self._permissions.authorize(request)
            except PermissionDenied as exc:
                return ToolExecution(str(exc), is_error=True)
            return await self._router.dispatch(
                invocation, granted_access=grant.access, mode=mode, context=context
            )

    def resolve_approval(
        self, call_id: str, approved: bool, submission_id: int
    ) -> bool:
        """只解析仍在等待且属于目标回合的批准；迟到答复不会获得新权限。"""

        return self._permissions.resolve(call_id, approved, submission_id)

    def cancel(self, call_id: str) -> bool:
        """请求取消一个已登记的工具；返回值供后续入口层决定是否提示未找到。"""

        task = self._running.get(call_id)
        if task is None or task.done():
            return False
        self._cancel_requested.add(call_id)
        task.cancel()
        return True
