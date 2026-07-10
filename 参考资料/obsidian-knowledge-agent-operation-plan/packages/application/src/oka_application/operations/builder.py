from __future__ import annotations

from datetime import timedelta
from typing import Sequence

from oka_domain.common import IdempotencyKey, ResourceType, VaultPath, utc_now
from oka_domain.operations import (
    AffectedResource,
    ApproverType,
    ConfirmationMode,
    ConfirmationPolicy,
    FailurePolicy,
    KnowledgeOperation,
    OperationPlan,
    OperationSource,
    OperationType,
    OperationWarning,
    ResourceAction,
    RiskLevel,
    RollbackMode,
    RollbackPolicy,
    WarningSeverity,
)

from .risk import RiskCalculator


class OperationPlanBuilder:
    def __init__(self, risk_calculator: RiskCalculator | None = None) -> None:
        self.risk_calculator = risk_calculator or RiskCalculator()

    def build(
        self,
        *,
        instruction: str,
        summary: str,
        source: OperationSource,
        operations: Sequence[KnowledgeOperation],
        idempotency_key: IdempotencyKey,
        affected_resources: Sequence[AffectedResource] | None = None,
        failure_policy: FailurePolicy | None = None,
        rollback_policy: RollbackPolicy | None = None,
    ) -> OperationPlan:
        assessment = self.risk_calculator.assess(operations=operations, source=source)
        resources = tuple(affected_resources or self._infer_resources(assessment.operations))
        policy = rollback_policy or self._rollback_policy(assessment.plan_risk)
        failures = failure_policy or self._failure_policy(assessment.operations, assessment.plan_risk)
        confirmation = self._confirmation_policy(assessment.plan_risk, source)
        expires_at = utc_now() + self._expiry(assessment.plan_risk)
        warnings = tuple(
            OperationWarning(
                code="OPERATION_RISK_NOTICE",
                message=message,
                severity=WarningSeverity.WARNING,
            )
            for message in assessment.warnings
        )
        return OperationPlan.create(
            instruction=instruction,
            summary=summary,
            source=source,
            operations=assessment.operations,
            affected_resources=resources,
            rollback_policy=policy,
            failure_policy=failures,
            idempotency_key=idempotency_key,
            warnings=warnings,
            confirmation_policy=confirmation,
            expires_at=expires_at,
        )

    @staticmethod
    def _confirmation_policy(risk: RiskLevel, source: OperationSource) -> ConfirmationPolicy:
        if risk is RiskLevel.LOW and not source.remote:
            return ConfirmationPolicy(
                mode=ConfirmationMode.NONE,
                approver_types=(ApproverType.LOCAL_USER,),
            )
        return ConfirmationPolicy(
            mode=ConfirmationMode.EXPLICIT,
            approver_types=(ApproverType.LOCAL_USER, ApproverType.ADMINISTRATOR),
            reason="Plan contains writes that require an explicit local approval",
        )

    @staticmethod
    def _rollback_policy(risk: RiskLevel) -> RollbackPolicy:
        if risk is RiskLevel.LOW:
            return RollbackPolicy(
                mode=RollbackMode.BEST_EFFORT,
                snapshot_required=False,
                retention_seconds=86_400,
            )
        return RollbackPolicy(
            mode=RollbackMode.REQUIRED if risk >= RiskLevel.HIGH else RollbackMode.BEST_EFFORT,
            snapshot_required=True,
            retention_seconds=604_800,
            max_rollback_age_seconds=604_800,
        )

    @staticmethod
    def _failure_policy(
        operations: Sequence[KnowledgeOperation], risk: RiskLevel
    ) -> FailurePolicy:
        if risk >= RiskLevel.HIGH or any(item.depends_on for item in operations):
            return FailurePolicy.ROLLBACK_ALL
        return FailurePolicy.STOP

    @staticmethod
    def _expiry(risk: RiskLevel) -> timedelta:
        return {
            RiskLevel.LOW: timedelta(hours=24),
            RiskLevel.MEDIUM: timedelta(hours=2),
            RiskLevel.HIGH: timedelta(minutes=30),
            RiskLevel.CRITICAL: timedelta(minutes=5),
        }[risk]

    def _infer_resources(
        self, operations: Sequence[KnowledgeOperation]
    ) -> tuple[AffectedResource, ...]:
        resources: list[AffectedResource] = []
        seen: set[tuple[str, str, str]] = set()
        for operation in operations:
            for resource in self._resources_for_operation(operation):
                key = (
                    resource.resource_type.value,
                    resource.action.value,
                    str(resource.path or resource.resource_id),
                )
                if key not in seen:
                    resources.append(resource)
                    seen.add(key)
        return tuple(resources)

    @staticmethod
    def _resources_for_operation(operation: KnowledgeOperation) -> tuple[AffectedResource, ...]:
        value = operation.input
        if operation.operation_type is OperationType.CREATE_NOTE:
            return (AffectedResource(resource_type=ResourceType.NOTE, action=ResourceAction.CREATE, path=value.path),)
        if operation.operation_type is OperationType.UPDATE_NOTE:
            return (AffectedResource(resource_type=ResourceType.NOTE, action=ResourceAction.UPDATE, path=value.path),)
        if operation.operation_type is OperationType.MOVE_NOTE:
            return (
                AffectedResource(resource_type=ResourceType.NOTE, action=ResourceAction.MOVE, path=value.source_path),
                AffectedResource(resource_type=ResourceType.NOTE, action=ResourceAction.CREATE, path=value.target_path),
            )
        if operation.operation_type is OperationType.UPDATE_METADATA:
            return (AffectedResource(resource_type=ResourceType.NOTE, action=ResourceAction.UPDATE, path=value.path),)
        if operation.operation_type is OperationType.CREATE_TASK:
            return (AffectedResource(resource_type=ResourceType.TASK, action=ResourceAction.CREATE, path=value.target_path),)
        if operation.operation_type is OperationType.INVOKE_PLUGIN:
            return (AffectedResource(resource_type=ResourceType.PLUGIN, action=ResourceAction.INVOKE, resource_id=None, path=VaultPath(f"Plugins/{value.plugin_id}")),)
        return ()
