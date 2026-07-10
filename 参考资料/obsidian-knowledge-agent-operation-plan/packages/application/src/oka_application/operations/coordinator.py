from __future__ import annotations

from datetime import timedelta

from oka_domain.common import IdempotencyKey, PlanId, utc_now
from oka_domain.operations import FailurePolicy, OperationPlanRepository, PlanStatus

from .errors import (
    OperationApplicationError,
    OperationConfirmationError,
    OperationExecutionLocked,
)
from .models import (
    AuditRecord,
    ExecutionContext,
    ExecutionStatus,
    OperationError,
    OperationExecution,
    OperationResult,
    OperationResultStatus,
)
from .ports import (
    AuditRepository,
    ConfirmationTokenService,
    ExecutionLock,
    OperationExecutionRepository,
    OperationIdempotencyStore,
    SnapshotStore,
)
from .registry import OperationHandlerRegistry
from .rollback import RollbackManager
from .validator import OperationPlanValidator


class ExecutionCoordinator:
    def __init__(
        self,
        *,
        plans: OperationPlanRepository,
        executions: OperationExecutionRepository,
        idempotency: OperationIdempotencyStore,
        lock: ExecutionLock,
        snapshots: SnapshotStore,
        audit: AuditRepository,
        tokens: ConfirmationTokenService,
        validator: OperationPlanValidator,
        registry: OperationHandlerRegistry,
        rollback_manager: RollbackManager,
        lease_ttl_seconds: int = 60,
    ) -> None:
        self.plans = plans
        self.executions = executions
        self.idempotency = idempotency
        self.lock = lock
        self.snapshots = snapshots
        self.audit = audit
        self.tokens = tokens
        self.validator = validator
        self.registry = registry
        self.rollback_manager = rollback_manager
        self.lease_ttl_seconds = lease_ttl_seconds

    async def execute(
        self,
        *,
        plan_id: PlanId,
        plan_version: int,
        context: ExecutionContext,
        confirmation_token: str | None = None,
    ) -> OperationExecution:
        existing = await self.executions.get_by_plan(plan_id, plan_version)
        if existing is not None and existing.status in {
            ExecutionStatus.COMPLETED,
            ExecutionStatus.PARTIALLY_COMPLETED,
            ExecutionStatus.ROLLED_BACK,
        }:
            return existing

        plan = await self.plans.get(plan_id)
        if plan is None:
            raise OperationApplicationError("Operation plan not found")
        if plan.plan_version != plan_version:
            raise OperationApplicationError("Operation plan version mismatch")
        if plan.requires_confirmation:
            if not confirmation_token:
                raise OperationConfirmationError("Confirmation token is required")
            claims = self.tokens.verify(confirmation_token, consume=False)
            if claims.plan_id != plan.plan_id or claims.plan_version != plan.plan_version:
                raise OperationConfirmationError("Confirmation token targets another plan")
            if claims.integrity_hash != plan.integrity:
                raise OperationConfirmationError("Confirmation token integrity does not match plan")
            approved_ids = {item.operation_id for item in plan.approved_operations()}
            if set(claims.approved_operation_ids) != approved_ids:
                raise OperationConfirmationError("Confirmation token approval scope is invalid")

        await self.validator.validate_for_execution(plan, context)
        execution = OperationExecution.start(
            plan_id=plan.plan_id,
            plan_version=plan.plan_version,
            executor_type=context.executor_type,
            executor_instance_id=context.executor_instance_id,
        )
        lease = await self.lock.acquire(
            plan_id=plan.plan_id,
            execution_id=execution.execution_id,
            owner_instance_id=context.executor_instance_id,
            ttl_seconds=self.lease_ttl_seconds,
        )
        if lease is None:
            raise OperationExecutionLocked("Another executor owns this plan")
        if confirmation_token is not None:
            self.tokens.verify(confirmation_token, consume=True)

        await self._audit(plan, execution, context, "operation-execution.started", "running")
        try:
            plan.start_execution()
            await self.plans.save(plan)
            await self.executions.save(execution)
            failed = False
            approved = plan.approved_operations()
            results_by_id: dict[str, OperationResult] = {}

            for operation in approved:
                blocked = [
                    dep
                    for dep in operation.depends_on
                    if str(dep) not in results_by_id
                    or results_by_id[str(dep)].status is not OperationResultStatus.COMPLETED
                ]
                if blocked:
                    result = OperationResult(
                        operation_id=operation.operation_id,
                        status=OperationResultStatus.SKIPPED,
                        error=OperationError(
                            code="OPERATION_DEPENDENCY_FAILED",
                            message="A required operation did not complete",
                            details={"dependencies": [str(item) for item in blocked]},
                        ),
                    )
                    execution.add_result(result)
                    results_by_id[str(operation.operation_id)] = result
                    failed = True
                    continue

                cached = await self.idempotency.get(operation.idempotency_key)
                if cached is not None:
                    execution.add_result(cached)
                    results_by_id[str(operation.operation_id)] = cached
                    continue

                runtime_operation = self._resolve_runtime_bindings(operation, results_by_id)
                handler = self.registry.resolve(runtime_operation.operation_type)
                try:
                    prepared = await handler.prepare(runtime_operation, context)
                    snapshot_ref = None
                    if prepared.rollback_data is not None:
                        snapshot = await self.snapshots.save(
                            execution_id=execution.execution_id,
                            operation_id=operation.operation_id,
                            rollback_data=prepared.rollback_data,
                            expires_at=utc_now()
                            + timedelta(seconds=plan.rollback_policy.retention_seconds),
                        )
                        snapshot_ref = str(snapshot.snapshot_id)
                    result = await handler.execute(prepared, context)
                    if snapshot_ref is not None:
                        result = OperationResult(
                            operation_id=result.operation_id,
                            status=result.status,
                            attempt_count=result.attempt_count,
                            before_version_hash=result.before_version_hash,
                            after_version_hash=result.after_version_hash,
                            affected_paths=result.affected_paths,
                            rollback_available=True,
                            rollback_data_ref=snapshot_ref,
                            error=result.error,
                            metadata=result.metadata,
                        )
                    await self.idempotency.put(operation.idempotency_key, result)
                except Exception as exc:
                    error = self._error(exc)
                    result = OperationResult(
                        operation_id=operation.operation_id,
                        status=OperationResultStatus.FAILED,
                        error=error,
                    )
                    failed = True

                execution.add_result(result)
                results_by_id[str(operation.operation_id)] = result
                await self.executions.save(execution)
                await self._audit(
                    plan,
                    execution,
                    context,
                    "operation.completed" if result.status is OperationResultStatus.COMPLETED else "operation.failed",
                    result.status.value,
                    operation_result=result,
                )

                if result.status is OperationResultStatus.FAILED:
                    if plan.failure_policy is FailurePolicy.ROLLBACK_ALL:
                        has_completed_rollback = any(
                            item.status is OperationResultStatus.COMPLETED
                            and item.rollback_data_ref is not None
                            for item in execution.operation_results
                        )
                        if has_completed_rollback:
                            plan.mark_failed()
                            await self.plans.save(plan)
                            await self.rollback_manager.rollback_completed(
                                plan=plan,
                                execution=execution,
                                context=context,
                            )
                            await self._audit(
                                plan,
                                execution,
                                context,
                                "operation-execution.rolled-back",
                                execution.status.value,
                            )
                            return execution
                        break
                    if plan.failure_policy is FailurePolicy.STOP:
                        break

            completed_count = sum(
                result.status is OperationResultStatus.COMPLETED
                for result in execution.operation_results
            )
            failed_count = sum(
                result.status in {OperationResultStatus.FAILED, OperationResultStatus.SKIPPED}
                for result in execution.operation_results
            )
            if failed_count == 0:
                plan.mark_completed()
                execution.finish(ExecutionStatus.COMPLETED)
            elif completed_count > 0:
                plan.mark_failed()
                execution.finish(ExecutionStatus.PARTIALLY_COMPLETED)
            else:
                plan.mark_failed()
                execution.finish(ExecutionStatus.FAILED)
            await self.plans.save(plan)
            await self.executions.save(execution)
            await self._audit(plan, execution, context, "operation-execution.completed", execution.status.value)
            return execution
        except Exception as exc:
            if plan.status is PlanStatus.EXECUTING:
                plan.mark_failed()
                await self.plans.save(plan)
            execution.finish(ExecutionStatus.FAILED, error=self._error(exc))
            await self.executions.save(execution)
            await self._audit(plan, execution, context, "operation-execution.failed", "failed")
            raise
        finally:
            await self.lock.release(lease)

    async def _audit(
        self,
        plan,
        execution,
        context,
        event_type: str,
        result: str,
        operation_result: OperationResult | None = None,
    ) -> None:
        await self.audit.append(
            AuditRecord.create(
                event_type=event_type,
                plan_id=plan.plan_id,
                plan_version=plan.plan_version,
                execution_id=execution.execution_id,
                operation_id=operation_result.operation_id if operation_result else None,
                actor_id=context.actor_id,
                actor_type=context.actor_type,
                trace_id=context.trace_id,
                result=result,
                affected_paths=operation_result.affected_paths if operation_result else (),
            )
        )

    @staticmethod
    def _resolve_runtime_bindings(operation, results_by_id):
        from dataclasses import replace
        from oka_domain.operations import MoveNoteInput, UpdateNoteInput

        source_id = operation.extensions.get("expectedVersionFromOperation")
        if not source_id:
            return operation
        result = results_by_id.get(str(source_id))
        if result is None or result.after_version_hash is None:
            raise OperationApplicationError(
                "Runtime version binding cannot be resolved",
                details={
                    "operationId": str(operation.operation_id),
                    "sourceOperationId": str(source_id),
                },
            )
        value = operation.input
        if isinstance(value, (MoveNoteInput, UpdateNoteInput)):
            return replace(operation, input=replace(value, expected_version_hash=result.after_version_hash))
        raise OperationApplicationError(
            "expectedVersionFromOperation is unsupported for this operation type",
            details={"operationId": str(operation.operation_id)},
        )

    @staticmethod
    def _error(exc: Exception) -> OperationError:
        if isinstance(exc, OperationApplicationError):
            return OperationError(
                code=exc.code,
                message=exc.message,
                retryable=exc.retryable,
                details=exc.details,
            )
        return OperationError(code="OPERATION_INTERNAL_ERROR", message=str(exc), retryable=False)
