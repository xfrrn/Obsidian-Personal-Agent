from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from oka_application.operations.models import (
    AuditRecord,
    ExecutionId,
    ExecutionLease,
    OperationExecution,
    OperationResult,
    RollbackData,
    SnapshotId,
    StoredSnapshot,
)
from oka_domain.common import IdempotencyKey, Identifier, OperationId, PlanId, utc_now
from oka_domain.operations import OperationPlan, PlanStatus

from .serialization import (
    execution_from_dict,
    execution_to_dict,
    operation_plan_from_dict,
    operation_plan_to_dict,
    operation_result_from_dict,
    operation_result_to_dict,
)


def _atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    os.replace(temporary, path)


class JsonOperationPlanRepository:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.plan_dir = self.root / "plans"
        self.index_path = self.root / "plan-idempotency.json"
        self._guard = asyncio.Lock()
        self.plan_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, plan_id: PlanId) -> Path:
        return self.plan_dir / f"{plan_id}.json"

    async def get(self, plan_id: PlanId) -> OperationPlan | None:
        path = self._path(plan_id)
        if not path.is_file():
            return None
        return operation_plan_from_dict(json.loads(path.read_text(encoding="utf-8")))

    async def get_by_idempotency_key(self, key: IdempotencyKey) -> OperationPlan | None:
        if not self.index_path.is_file():
            return None
        index = json.loads(self.index_path.read_text(encoding="utf-8"))
        plan_id = index.get(str(key))
        return await self.get(PlanId(plan_id)) if plan_id else None

    async def list_by_status(self, statuses):
        allowed = {item.value if isinstance(item, PlanStatus) else str(item) for item in statuses}
        result = []
        for path in self.plan_dir.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("status") in allowed:
                result.append(operation_plan_from_dict(data))
        return result

    async def save(self, plan: OperationPlan) -> None:
        async with self._guard:
            _atomic_json(self._path(plan.plan_id), operation_plan_to_dict(plan))
            index = {}
            if self.index_path.is_file():
                index = json.loads(self.index_path.read_text(encoding="utf-8"))
            index[str(plan.idempotency_key)] = str(plan.plan_id)
            _atomic_json(self.index_path, index)


class JsonExecutionRepository:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.execution_dir = self.root / "executions"
        self.index_path = self.root / "execution-plan-index.json"
        self._guard = asyncio.Lock()
        self.execution_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, execution_id: ExecutionId) -> Path:
        return self.execution_dir / f"{execution_id}.json"

    async def get(self, execution_id: ExecutionId) -> OperationExecution | None:
        path = self._path(execution_id)
        if not path.is_file():
            return None
        return execution_from_dict(json.loads(path.read_text(encoding="utf-8")))

    async def get_by_plan(self, plan_id: PlanId, plan_version: int) -> OperationExecution | None:
        if not self.index_path.is_file():
            return None
        index = json.loads(self.index_path.read_text(encoding="utf-8"))
        execution_id = index.get(f"{plan_id}:{plan_version}")
        return await self.get(ExecutionId(execution_id)) if execution_id else None

    async def save(self, execution: OperationExecution) -> None:
        async with self._guard:
            _atomic_json(self._path(execution.execution_id), execution_to_dict(execution))
            index = {}
            if self.index_path.is_file():
                index = json.loads(self.index_path.read_text(encoding="utf-8"))
            index[f"{execution.plan_id}:{execution.plan_version}"] = str(execution.execution_id)
            _atomic_json(self.index_path, index)


class JsonIdempotencyStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve() / "idempotency"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: IdempotencyKey) -> Path:
        digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    async def get(self, key: IdempotencyKey) -> OperationResult | None:
        path = self._path(key)
        if not path.is_file():
            return None
        return operation_result_from_dict(json.loads(path.read_text(encoding="utf-8")))

    async def put(self, key: IdempotencyKey, result: OperationResult) -> None:
        _atomic_json(self._path(key), operation_result_to_dict(result))

    async def delete(self, key: IdempotencyKey) -> None:
        self._path(key).unlink(missing_ok=True)


class FileSnapshotStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    async def save(self, *, execution_id, operation_id, rollback_data, expires_at):
        snapshot = StoredSnapshot(
            snapshot_id=SnapshotId.new(),
            execution_id=execution_id,
            operation_id=operation_id,
            rollback_data=rollback_data,
            expires_at=expires_at,
        )
        path = self.root / str(execution_id) / f"{snapshot.snapshot_id}.json"
        _atomic_json(
            path,
            {
                "snapshotId": str(snapshot.snapshot_id),
                "executionId": str(execution_id),
                "operationId": str(operation_id),
                "rollbackData": {
                    "kind": rollback_data.kind,
                    "payload": dict(rollback_data.payload),
                    "sensitive": rollback_data.sensitive,
                },
                "createdAt": snapshot.created_at.isoformat(),
                "expiresAt": expires_at.isoformat() if expires_at else None,
            },
        )
        return snapshot

    async def get(self, snapshot_id: str) -> StoredSnapshot | None:
        matches = list(self.root.glob(f"*/{snapshot_id}.json"))
        if not matches:
            return None
        data = json.loads(matches[0].read_text(encoding="utf-8"))
        expires_at = None
        if data.get("expiresAt"):
            expires_at = datetime.fromisoformat(data["expiresAt"])
            if utc_now() >= expires_at:
                return None
        rollback = data["rollbackData"]
        return StoredSnapshot(
            snapshot_id=SnapshotId(data["snapshotId"]),
            execution_id=ExecutionId(data["executionId"]),
            operation_id=OperationId(data["operationId"]),
            rollback_data=RollbackData(
                kind=rollback["kind"],
                payload=dict(rollback["payload"]),
                sensitive=bool(rollback.get("sensitive", True)),
            ),
            created_at=datetime.fromisoformat(data["createdAt"]),
            expires_at=expires_at,
        )

    async def delete(self, snapshot_id: str) -> None:
        for path in self.root.glob(f"*/{snapshot_id}.json"):
            path.unlink(missing_ok=True)


class JsonlAuditRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._guard = asyncio.Lock()

    async def append(self, record: AuditRecord) -> None:
        data = {
            "auditId": str(record.audit_id),
            "eventType": record.event_type,
            "planId": str(record.plan_id),
            "planVersion": record.plan_version,
            "executionId": str(record.execution_id) if record.execution_id else None,
            "operationId": str(record.operation_id) if record.operation_id else None,
            "actorId": str(record.actor_id),
            "actorType": record.actor_type.value,
            "timestamp": record.timestamp.isoformat(),
            "traceId": str(record.trace_id),
            "result": record.result,
            "affectedPaths": [str(item) for item in record.affected_paths],
            "details": dict(record.details),
        }
        async with self._guard:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")

    async def list_for_plan(self, plan_id: PlanId):
        if not self.path.is_file():
            return []
        from oka_application.operations.models import AuditId
        from oka_domain.operations import ActorType
        from oka_domain.common import VaultPath

        result = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            data = json.loads(line)
            if data["planId"] != str(plan_id):
                continue
            result.append(
                AuditRecord(
                    audit_id=AuditId(data["auditId"]),
                    event_type=data["eventType"],
                    plan_id=PlanId(data["planId"]),
                    plan_version=int(data["planVersion"]),
                    execution_id=ExecutionId(data["executionId"]) if data.get("executionId") else None,
                    operation_id=OperationId(data["operationId"]) if data.get("operationId") else None,
                    actor_id=Identifier(data["actorId"]),
                    actor_type=ActorType(data["actorType"]),
                    timestamp=datetime.fromisoformat(data["timestamp"]),
                    trace_id=Identifier(data["traceId"]),
                    result=data.get("result"),
                    affected_paths=tuple(VaultPath(item) for item in data.get("affectedPaths", [])),
                    details=dict(data.get("details", {})),
                )
            )
        return result


class FileExecutionLock:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, plan_id: PlanId) -> Path:
        return self.root / f"{plan_id}.lock"

    async def acquire(self, *, plan_id, execution_id, owner_instance_id, ttl_seconds):
        path = self._path(plan_id)
        now = utc_now()
        if path.exists():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
                if datetime.fromisoformat(current["expiresAt"]) > now:
                    return None
            except Exception:
                return None
            path.unlink(missing_ok=True)
        lease = ExecutionLease(
            plan_id=plan_id,
            execution_id=execution_id,
            owner_instance_id=owner_instance_id,
            acquired_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        payload = {
            "planId": str(plan_id),
            "executionId": str(execution_id),
            "ownerInstanceId": str(owner_instance_id),
            "acquiredAt": lease.acquired_at.isoformat(),
            "expiresAt": lease.expires_at.isoformat(),
        }
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return None
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        return lease

    async def renew(self, lease: ExecutionLease, ttl_seconds: int) -> ExecutionLease:
        path = self._path(lease.plan_id)
        if not path.is_file():
            raise RuntimeError("Execution lease no longer exists")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("executionId") != str(lease.execution_id):
            raise RuntimeError("Execution lease is owned by another execution")
        renewed = ExecutionLease(
            plan_id=lease.plan_id,
            execution_id=lease.execution_id,
            owner_instance_id=lease.owner_instance_id,
            acquired_at=lease.acquired_at,
            expires_at=utc_now() + timedelta(seconds=ttl_seconds),
        )
        _atomic_json(
            path,
            {
                "planId": str(lease.plan_id),
                "executionId": str(lease.execution_id),
                "ownerInstanceId": str(lease.owner_instance_id),
                "acquiredAt": lease.acquired_at.isoformat(),
                "expiresAt": renewed.expires_at.isoformat(),
            },
        )
        return renewed

    async def release(self, lease: ExecutionLease) -> None:
        path = self._path(lease.plan_id)
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("executionId") == str(lease.execution_id):
                path.unlink(missing_ok=True)
        except Exception:
            return
