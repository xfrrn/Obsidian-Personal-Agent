"""工具权限策略。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .definitions import ToolDefinition, ToolPermission, ToolRiskLevel

_RISK_ORDER = {
    ToolRiskLevel.LOW: 0,
    ToolRiskLevel.MEDIUM: 1,
    ToolRiskLevel.HIGH: 2,
}


@dataclass(frozen=True)
class PermissionDecision:
    """工具是否允许运行的检查结果。"""

    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class ToolPolicy:
    """工具执行所需的最小权限策略。"""

    allowed_permissions: frozenset[ToolPermission] = frozenset({ToolPermission.READ})
    max_risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    require_confirmation_for: frozenset[ToolRiskLevel] = frozenset({ToolRiskLevel.HIGH})

    @classmethod
    def allow(
        cls,
        permissions: Iterable[ToolPermission],
        max_risk_level: ToolRiskLevel = ToolRiskLevel.LOW,
    ) -> "ToolPolicy":
        """根据权限集合和最高风险级别创建策略。"""
        return cls(frozenset(permissions), max_risk_level)

    def check(
        self,
        definition: ToolDefinition,
        *,
        confirmed: bool = False,
    ) -> PermissionDecision:
        """判断某个工具定义是否被当前策略允许。"""
        if definition.permission not in self.allowed_permissions:
            return PermissionDecision(False, f"permission denied: {definition.permission.value}")
        if _RISK_ORDER[definition.risk_level] > _RISK_ORDER[self.max_risk_level]:
            return PermissionDecision(False, f"risk too high: {definition.risk_level.value}")
        if definition.risk_level in self.require_confirmation_for and not confirmed:
            return PermissionDecision(False, "confirmation required")
        return PermissionDecision(True)
