from __future__ import annotations

from oka_domain.operations import OperationPlan, OperationPlanRepository

from .errors import OperationRollbackError
from .models import ExecutionContext, ExecutionStatus, OperationExecution, OperationResultStatus
from .ports import (
    AuditRepository,
    OperationExecutionRepository,
    OperationIdempotencyStore,
    SnapshotStore,
)
from .registry import OperationHandlerRegistry


class RollbackManager:
    def __init__(
        self,
        *,
        plans: OperationPlanRepository,
        executions: OperationExecutionRepository,
        snapshots: SnapshotStore,
        audit: AuditRepository,
        registry: OperationHandlerRegistry,
        idempotency: OperationIdempotencyStore,
    ) -> None:
        self.plans = plans
        self.executions = executions
        self.snapshots = snapshots
        self.audit = audit
        self.registry = registry
        self.idempotency = idempotency

    async def rollback_completed(
        self,
        *,
        plan: OperationPlan,
        execution: OperationExecution,
        context: ExecutionContext,
    ) -> bool:
        execution.status = ExecutionStatus.ROLLING_BACK
        await self.executions.save(execution)
        operation_map = {str(item.operation_id): item for item in plan.operations}
        success = True
        new_results = list(execution.operation_results)
        for index in range(len(new_results) - 1, -1, -1):
            result = new_results[index]
            if result.status is not OperationResultStatus.COMPLETED or not result.rollback_data_ref:
                continue
            snapshot = await self.snapshots.get(result.rollback_data_ref)
            if snapshot is None:
                success = False
                continue
            operation = operation_map.get(str(result.operation_id))
            if operation is None:
                success = False
                continue
            handler = self.registry.resolve(operation.operation_type)
            try:
                rolled = await handler.rollback(operation, snapshot.rollback_data, context)
                new_results[index] = rolled
                await self.idempotency.delete(operation.idempotency_key)
            except Exception:
                success = False
        execution.operation_results = new_results
        if success:
            execution.finish(ExecutionStatus.ROLLED_BACK)
            try:
                plan.mark_rolled_back()
            except Exception as exc:
                raise OperationRollbackError(str(exc)) from exc
        else:
            execution.finish(ExecutionStatus.ROLLBACK_FAILED)
        await self.executions.save(execution)
        await self.plans.save(plan)
        return success
