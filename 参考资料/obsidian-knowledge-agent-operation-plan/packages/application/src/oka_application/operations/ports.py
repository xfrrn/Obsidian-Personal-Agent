from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

from oka_domain.common import IdempotencyKey, OperationId, PlanId
from oka_domain.operations import KnowledgeOperation, OperationPlan, OperationPrecondition

from .models import (
    AuditRecord,
    ConfirmationClaims,
    ExecutionContext,
    ExecutionId,
    ExecutionLease,
    OperationExecution,
    OperationPlanPreview,
    OperationResult,
    PreparedOperation,
    ResourceDiff,
    ResourceState,
    RollbackData,
    StoredSnapshot,
)


class OperationExecutionRepository(Protocol):
    async def get(self, execution_id: ExecutionId) -> OperationExecution | None: ...

    async def get_by_plan(self, plan_id: PlanId, plan_version: int) -> OperationExecution | None: ...

    async def save(self, execution: OperationExecution) -> None: ...


class OperationIdempotencyStore(Protocol):
    async def get(self, key: IdempotencyKey) -> OperationResult | None: ...

    async def put(self, key: IdempotencyKey, result: OperationResult) -> None: ...

    async def delete(self, key: IdempotencyKey) -> None: ...


class ExecutionLock(Protocol):
    async def acquire(
        self,
        *,
        plan_id: PlanId,
        execution_id: ExecutionId,
        owner_instance_id: object,
        ttl_seconds: int,
    ) -> ExecutionLease | None: ...

    async def renew(self, lease: ExecutionLease, ttl_seconds: int) -> ExecutionLease: ...

    async def release(self, lease: ExecutionLease) -> None: ...


class SnapshotStore(Protocol):
    async def save(
        self,
        *,
        execution_id: ExecutionId,
        operation_id: OperationId,
        rollback_data: RollbackData,
        expires_at: datetime | None,
    ) -> StoredSnapshot: ...

    async def get(self, snapshot_id: str) -> StoredSnapshot | None: ...

    async def delete(self, snapshot_id: str) -> None: ...


class AuditRepository(Protocol):
    async def append(self, record: AuditRecord) -> None: ...

    async def list_for_plan(self, plan_id: PlanId) -> Sequence[AuditRecord]: ...


class ConfirmationTokenService(Protocol):
    def issue(self, claims: ConfirmationClaims) -> str: ...

    def verify(self, token: str, *, consume: bool = True) -> ConfirmationClaims: ...


class PermissionAuthorizer(Protocol):
    async def authorize_plan(self, plan: OperationPlan, context: ExecutionContext) -> None: ...

    async def authorize_operation(
        self,
        operation: KnowledgeOperation,
        context: ExecutionContext,
    ) -> None: ...


class RuntimeInspector(Protocol):
    async def state_for_operation(self, operation: KnowledgeOperation) -> tuple[ResourceState, ...]: ...

    async def check_precondition(
        self,
        precondition: OperationPrecondition,
        context: ExecutionContext,
    ) -> None: ...


class OperationHandler(Protocol):
    operation_type: object

    async def prepare(
        self,
        operation: KnowledgeOperation,
        context: ExecutionContext,
    ) -> PreparedOperation: ...

    async def execute(
        self,
        prepared: PreparedOperation,
        context: ExecutionContext,
    ) -> OperationResult: ...

    async def rollback(
        self,
        operation: KnowledgeOperation,
        rollback_data: RollbackData,
        context: ExecutionContext,
    ) -> OperationResult: ...

    async def preview(
        self,
        operation: KnowledgeOperation,
        context: ExecutionContext,
    ) -> ResourceDiff | None: ...


class PlanPreviewService(Protocol):
    async def build(self, plan: OperationPlan, context: ExecutionContext) -> OperationPlanPreview: ...
