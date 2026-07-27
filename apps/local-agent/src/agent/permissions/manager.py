"""权限决策和一次性审批的唯一状态边界。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from agent.permissions.models import (
    PermissionDecisionKind,
    PermissionGrant,
    PermissionRequest,
)
from agent.permissions.policy import PermissionPolicy


class PermissionDenied(PermissionError):
    """权限策略或用户决定拒绝了本次调用。"""


class PermissionManager:
    """签发调用级 Grant，并确保审批不能跨调用或跨回合复用。"""

    def __init__(
        self,
        policy: PermissionPolicy = PermissionPolicy(),
        approval_requested: Callable[[PermissionRequest], None] | None = None,
    ) -> None:
        self._policy = policy
        self._approval_requested = approval_requested
        self._pending: dict[str, tuple[int, asyncio.Future[bool]]] = {}

    async def authorize(self, request: PermissionRequest) -> PermissionGrant:
        decision = self._policy.decide(request.requirement, request.mode)
        if decision.kind is PermissionDecisionKind.DENY:
            raise PermissionDenied(decision.reason or "权限策略拒绝了本次工具调用。")
        if decision.kind is PermissionDecisionKind.REQUIRE_APPROVAL:
            if request.submission_id is None or self._approval_requested is None:
                raise PermissionDenied("权限策略拒绝：当前入口无法完成本次宿主执行审批。")
            if not await self._request_approval(request):
                raise PermissionDenied("用户拒绝了本次宿主执行请求。")
        return PermissionGrant(
            submission_id=request.submission_id,
            call_id=request.call_id,
            tool_name=request.tool_name,
            access=request.requirement.access,
        )

    async def _request_approval(self, request: PermissionRequest) -> bool:
        assert request.submission_id is not None
        if request.call_id in self._pending:
            return False
        decision = asyncio.get_running_loop().create_future()
        self._pending[request.call_id] = (request.submission_id, decision)
        try:
            # 先登记 Future 再发事件，避免入口立即答复时丢失批准。
            assert self._approval_requested is not None
            self._approval_requested(request)
            return await decision
        finally:
            current = self._pending.get(request.call_id)
            if current is not None and current[1] is decision:
                del self._pending[request.call_id]

    def resolve(
        self, call_id: str, approved: bool, submission_id: int
    ) -> bool:
        """只解析仍在等待且属于目标回合的批准；迟到答复不会获得新权限。"""

        if (
            not isinstance(call_id, str)
            or not isinstance(approved, bool)
            or not isinstance(submission_id, int)
            or isinstance(submission_id, bool)
        ):
            return False
        pending = self._pending.get(call_id)
        if pending is None or pending[0] != submission_id:
            return False
        decision = pending[1]
        if decision.done():
            return False
        decision.set_result(approved)
        return True
