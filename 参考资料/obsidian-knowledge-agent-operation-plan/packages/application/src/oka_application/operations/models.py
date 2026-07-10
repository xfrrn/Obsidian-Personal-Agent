from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping
import uuid

from oka_domain.common import Identifier, OperationId, PlanId, Sha256Hash, VaultPath, utc_now
from oka_domain.operations import ActorType, KnowledgeOperation, RiskLevel


class ExecutionId(Identifier):
    @classmethod
    def new(cls, prefix: str = "exec") -> "ExecutionId":
        return cls(f"{prefix}_{uuid.uuid4().hex}")


class SnapshotId(Identifier):
    @classmethod
    def new(cls, prefix: str = "snapshot") -> "SnapshotId":
        return cls(f"{prefix}_{uuid.uuid4().hex}")


class AuditId(Identifier):
    @classmethod
    def new(cls, prefix: str = "audit") -> "AuditId":
        return cls(f"{prefix}_{uuid.uuid4().hex}")


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially-completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ROLLING_BACK = "rolling-back"
    ROLLED_BACK = "rolled-back"
    ROLLBACK_FAILED = "rollback-failed"


class OperationResultStatus(StrEnum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    ROLLED_BACK = "rolled-back"
    ROLLBACK_FAILED = "rollback-failed"


class ExecutorType(StrEnum):
    OBSIDIAN = "obsidian"
    HEADLESS = "headless"
    TEST = "test"


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationError:
    code: str
    message: str
    retryable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceState:
    path: VaultPath
    exists: bool
    version_hash: Sha256Hash | None = None
    content_hash: Sha256Hash | None = None
    metadata_hash: Sha256Hash | None = None
    size_bytes: int | None = None
    modified_at: datetime | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RollbackData:
    kind: str
    payload: Mapping[str, Any]
    sensitive: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedOperation:
    operation: KnowledgeOperation
    before_states: tuple[ResourceState, ...] = ()
    rollback_data: RollbackData | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationResult:
    operation_id: OperationId
    status: OperationResultStatus
    attempt_count: int = 1
    before_version_hash: Sha256Hash | None = None
    after_version_hash: Sha256Hash | None = None
    affected_paths: tuple[VaultPath, ...] = ()
    rollback_available: bool = False
    rollback_data_ref: str | None = None
    error: OperationError | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class OperationExecution:
    execution_id: ExecutionId
    plan_id: PlanId
    plan_version: int
    status: ExecutionStatus
    executor_type: ExecutorType
    executor_instance_id: Identifier
    started_at: datetime = field(default_factory=utc_now)
    completed_at: datetime | None = None
    operation_results: list[OperationResult] = field(default_factory=list)
    rollback_available: bool = False
    rollback_token_hash: Sha256Hash | None = None
    error: OperationError | None = None

    @classmethod
    def start(
        cls,
        *,
        plan_id: PlanId,
        plan_version: int,
        executor_type: ExecutorType,
        executor_instance_id: Identifier,
    ) -> "OperationExecution":
        return cls(
            execution_id=ExecutionId.new(),
            plan_id=plan_id,
            plan_version=plan_version,
            status=ExecutionStatus.RUNNING,
            executor_type=executor_type,
            executor_instance_id=executor_instance_id,
        )

    def add_result(self, result: OperationResult) -> None:
        self.operation_results.append(result)
        if result.rollback_available:
            self.rollback_available = True

    def finish(self, status: ExecutionStatus, *, error: OperationError | None = None) -> None:
        self.status = status
        self.error = error
        self.completed_at = utc_now()


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionContext:
    vault_id: Identifier
    actor_id: Identifier
    actor_type: ActorType
    executor_type: ExecutorType
    executor_instance_id: Identifier
    permissions: frozenset[str]
    trace_id: Identifier
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfirmationClaims:
    plan_id: PlanId
    plan_version: int
    integrity_hash: Sha256Hash
    approver_id: Identifier
    approved_operation_ids: tuple[OperationId, ...]
    issued_at: datetime
    expires_at: datetime
    nonce: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceDiff:
    path: VaultPath
    change_type: str
    before_excerpt: str | None = None
    after_excerpt: str | None = None
    metadata_changes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationPlanPreview:
    plan_id: PlanId
    plan_version: int
    summary: str
    risk_level: RiskLevel
    affected_file_count: int
    created_files: tuple[VaultPath, ...]
    updated_files: tuple[VaultPath, ...]
    moved_files: tuple[tuple[VaultPath, VaultPath], ...]
    plugin_invocations: tuple[str, ...]
    diffs: tuple[ResourceDiff, ...]
    warnings: tuple[str, ...]
    rollback_available: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class AuditRecord:
    audit_id: AuditId
    event_type: str
    plan_id: PlanId
    plan_version: int
    actor_id: Identifier
    actor_type: ActorType
    timestamp: datetime
    trace_id: Identifier
    execution_id: ExecutionId | None = None
    operation_id: OperationId | None = None
    result: str | None = None
    affected_paths: tuple[VaultPath, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, **kwargs: Any) -> "AuditRecord":
        return cls(audit_id=AuditId.new(), timestamp=utc_now(), **kwargs)


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredSnapshot:
    snapshot_id: SnapshotId
    execution_id: ExecutionId
    operation_id: OperationId
    rollback_data: RollbackData
    created_at: datetime = field(default_factory=utc_now)
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionLease:
    plan_id: PlanId
    execution_id: ExecutionId
    owner_instance_id: Identifier
    acquired_at: datetime
    expires_at: datetime
