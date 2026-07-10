from __future__ import annotations

import asyncio
import copy
from datetime import timedelta

from oka_application.operations.models import (
    AuditRecord,
    ExecutionId,
    ExecutionLease,
    OperationExecution,
    OperationResult,
    SnapshotId,
    StoredSnapshot,
)
from oka_domain.common import IdempotencyKey, Identifier, PlanId, utc_now
from oka_domain.operations import OperationPlan, PlanStatus


class InMemoryOperationPlanRepository:
    def __init__(self) -> None:
        self._items: dict[str, OperationPlan] = {}
        self._keys: dict[str, str] = {}

    async def get(self, plan_id: PlanId) -> OperationPlan | None:
        item = self._items.get(str(plan_id))
        return copy.deepcopy(item) if item is not None else None

    async def get_by_idempotency_key(self, key: IdempotencyKey) -> OperationPlan | None:
        plan_id = self._keys.get(str(key))
        if plan_id is None:
            return None
        return copy.deepcopy(self._items[plan_id])

    async def list_by_status(self, statuses):
        allowed = set(statuses)
        return [copy.deepcopy(item) for item in self._items.values() if item.status in allowed]

    async def save(self, plan: OperationPlan) -> None:
        self._items[str(plan.plan_id)] = copy.deepcopy(plan)
        self._keys[str(plan.idempotency_key)] = str(plan.plan_id)


class InMemoryExecutionRepository:
    def __init__(self) -> None:
        self._items: dict[str, OperationExecution] = {}
        self._plans: dict[tuple[str, int], str] = {}

    async def get(self, execution_id: ExecutionId) -> OperationExecution | None:
        item = self._items.get(str(execution_id))
        return copy.deepcopy(item) if item is not None else None

    async def get_by_plan(self, plan_id: PlanId, plan_version: int) -> OperationExecution | None:
        execution_id = self._plans.get((str(plan_id), plan_version))
        if execution_id is None:
            return None
        return copy.deepcopy(self._items[execution_id])

    async def save(self, execution: OperationExecution) -> None:
        self._items[str(execution.execution_id)] = copy.deepcopy(execution)
        self._plans[(str(execution.plan_id), execution.plan_version)] = str(execution.execution_id)


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._items: dict[str, OperationResult] = {}

    async def get(self, key: IdempotencyKey) -> OperationResult | None:
        item = self._items.get(str(key))
        return copy.deepcopy(item) if item is not None else None

    async def put(self, key: IdempotencyKey, result: OperationResult) -> None:
        self._items[str(key)] = copy.deepcopy(result)

    async def delete(self, key: IdempotencyKey) -> None:
        self._items.pop(str(key), None)


class InMemorySnapshotStore:
    def __init__(self) -> None:
        self._items: dict[str, StoredSnapshot] = {}

    async def save(self, *, execution_id, operation_id, rollback_data, expires_at):
        item = StoredSnapshot(
            snapshot_id=SnapshotId.new(),
            execution_id=execution_id,
            operation_id=operation_id,
            rollback_data=copy.deepcopy(rollback_data),
            expires_at=expires_at,
        )
        self._items[str(item.snapshot_id)] = item
        return copy.deepcopy(item)

    async def get(self, snapshot_id: str) -> StoredSnapshot | None:
        item = self._items.get(snapshot_id)
        if item is None:
            return None
        if item.expires_at is not None and utc_now() >= item.expires_at:
            return None
        return copy.deepcopy(item)

    async def delete(self, snapshot_id: str) -> None:
        self._items.pop(snapshot_id, None)


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    async def append(self, record: AuditRecord) -> None:
        self.records.append(copy.deepcopy(record))

    async def list_for_plan(self, plan_id: PlanId):
        return [copy.deepcopy(item) for item in self.records if item.plan_id == plan_id]


class InMemoryExecutionLock:
    def __init__(self) -> None:
        self._leases: dict[str, ExecutionLease] = {}
        self._guard = asyncio.Lock()

    async def acquire(
        self,
        *,
        plan_id,
        execution_id,
        owner_instance_id,
        ttl_seconds,
    ) -> ExecutionLease | None:
        owner = owner_instance_id
        if not isinstance(owner, Identifier):
            owner = Identifier(str(owner))
        async with self._guard:
            key = str(plan_id)
            existing = self._leases.get(key)
            now = utc_now()
            if existing is not None and existing.expires_at > now:
                return None
            lease = ExecutionLease(
                plan_id=plan_id,
                execution_id=execution_id,
                owner_instance_id=owner,
                acquired_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
            self._leases[key] = lease
            return lease

    async def renew(self, lease: ExecutionLease, ttl_seconds: int) -> ExecutionLease:
        async with self._guard:
            current = self._leases.get(str(lease.plan_id))
            if current != lease:
                raise RuntimeError("Execution lease is no longer owned")
            renewed = ExecutionLease(
                plan_id=lease.plan_id,
                execution_id=lease.execution_id,
                owner_instance_id=lease.owner_instance_id,
                acquired_at=lease.acquired_at,
                expires_at=utc_now() + timedelta(seconds=ttl_seconds),
            )
            self._leases[str(lease.plan_id)] = renewed
            return renewed

    async def release(self, lease: ExecutionLease) -> None:
        async with self._guard:
            current = self._leases.get(str(lease.plan_id))
            if current == lease:
                self._leases.pop(str(lease.plan_id), None)
