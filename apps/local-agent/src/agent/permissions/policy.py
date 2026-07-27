"""无状态、可独立测试的工具权限策略。"""

from __future__ import annotations

from dataclasses import dataclass

from agent.permissions.models import (
    ApprovalPolicy,
    PermissionDecision,
    PermissionDecisionKind,
    PermissionRequirement,
    SandboxMode,
    ToolAccess,
)
from agent.protocol.mode import ModeKind


@dataclass(frozen=True, slots=True)
class PermissionPolicy:
    sandbox_mode: SandboxMode = SandboxMode.WORKSPACE_WRITE
    approval_policy: ApprovalPolicy = ApprovalPolicy.ON_REQUEST

    def decide(
        self,
        requirement: PermissionRequirement,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> PermissionDecision:
        access = requirement.access
        if mode is ModeKind.PLAN and access in {
            ToolAccess.WORKSPACE_WRITE,
            ToolAccess.HOST_EXECUTION,
        }:
            return PermissionDecision(
                PermissionDecisionKind.DENY,
                f"权限策略拒绝：Plan Mode 不允许 {access.value}。",
            )
        if access is ToolAccess.READ_ONLY:
            return PermissionDecision(PermissionDecisionKind.ALLOW)
        if access is ToolAccess.WORKSPACE_WRITE:
            if self.sandbox_mode is SandboxMode.READ_ONLY:
                return PermissionDecision(
                    PermissionDecisionKind.DENY,
                    "权限策略拒绝：当前沙盒模式为 read-only，工具需要写入工作区。",
                )
            return PermissionDecision(PermissionDecisionKind.ALLOW)
        if access is ToolAccess.OS_SANDBOX_EXECUTION:
            return PermissionDecision(PermissionDecisionKind.ALLOW)
        if access is not ToolAccess.HOST_EXECUTION:
            return PermissionDecision(
                PermissionDecisionKind.DENY,
                f"权限策略拒绝：无法识别工具能力 {access.value}。",
            )
        if self.sandbox_mode is SandboxMode.DANGER_FULL_ACCESS:
            return PermissionDecision(PermissionDecisionKind.ALLOW)
        if self.approval_policy is ApprovalPolicy.NEVER:
            return PermissionDecision(
                PermissionDecisionKind.DENY,
                f"权限策略拒绝：当前沙盒模式为 {self.sandbox_mode.value}，工具需要宿主执行，"
                "且审批策略为 never。",
            )
        if requirement.approval_reason:
            return PermissionDecision(PermissionDecisionKind.REQUIRE_APPROVAL)
        return PermissionDecision(
            PermissionDecisionKind.DENY,
            f"权限策略拒绝：当前沙盒模式为 {self.sandbox_mode.value}，工具需要宿主执行；"
            "只有显式请求并获得用户单次批准后才能运行。",
        )

    def denial_reason(
        self, access: ToolAccess, mode: ModeKind = ModeKind.DEFAULT
    ) -> str | None:
        """兼容原有只传能力的调用；新代码应使用 decide()。"""

        decision = self.decide(PermissionRequirement(access), mode)
        return decision.reason if decision.kind is PermissionDecisionKind.DENY else None
