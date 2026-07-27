"""权限系统跨层传递的不可变领域对象。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from agent.protocol.mode import ModeKind


class SandboxMode(str, Enum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"

    @classmethod
    def parse(cls, value: str) -> "SandboxMode":
        try:
            return cls(value.strip().lower())
        except ValueError as exc:
            choices = ", ".join(mode.value for mode in cls)
            raise ValueError(f"AGENT_SANDBOX_MODE 必须是: {choices}") from exc


class ApprovalPolicy(str, Enum):
    NEVER = "never"
    ON_REQUEST = "on-request"

    @classmethod
    def parse(cls, value: str) -> "ApprovalPolicy":
        try:
            return cls(value.strip().lower())
        except ValueError as exc:
            choices = ", ".join(policy.value for policy in cls)
            raise ValueError(f"AGENT_APPROVAL_POLICY 必须是: {choices}") from exc


class ToolAccess(str, Enum):
    """工具处理器必须声明的最小能力。"""

    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    OS_SANDBOX_EXECUTION = "os-sandbox-execution"
    HOST_EXECUTION = "host-execution"


@dataclass(frozen=True, slots=True)
class PermissionRequirement:
    """工具根据本次参数声明的实际权限需求。"""

    access: ToolAccess
    approval_reason: str | None = None


class PermissionDecisionKind(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require-approval"


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    """纯策略判断；审批的等待和生命周期由 PermissionManager 处理。"""

    kind: PermissionDecisionKind
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """将权限需求绑定到唯一工具调用，防止审批跨回合复用。"""

    submission_id: int | None
    call_id: str
    tool_name: str
    arguments: Mapping[str, object]
    requirement: PermissionRequirement
    mode: ModeKind = ModeKind.DEFAULT


@dataclass(frozen=True, slots=True)
class PermissionGrant:
    """PermissionManager 对单次工具调用签发的授权结果。"""

    submission_id: int | None
    call_id: str
    tool_name: str
    access: ToolAccess
