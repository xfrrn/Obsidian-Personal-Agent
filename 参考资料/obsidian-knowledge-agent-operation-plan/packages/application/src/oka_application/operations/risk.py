from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from oka_domain.common import VaultPath
from oka_domain.operations import (
    ActorType,
    ConflictPolicy,
    InvokePluginInput,
    KnowledgeOperation,
    MoveNoteInput,
    OperationSource,
    OperationType,
    RiskLevel,
    UpdateMode,
    UpdateNoteInput,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskPolicyConfig:
    protected_paths: tuple[VaultPath, ...] = ()
    bulk_high_threshold: int = 20
    bulk_critical_threshold: int = 100
    remote_minimum_risk: RiskLevel = RiskLevel.MEDIUM


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskAssessment:
    operations: tuple[KnowledgeOperation, ...]
    plan_risk: RiskLevel
    warnings: tuple[str, ...]


class RiskCalculator:
    def __init__(self, config: RiskPolicyConfig | None = None) -> None:
        self.config = config or RiskPolicyConfig()

    def assess(
        self,
        *,
        operations: Iterable[KnowledgeOperation],
        source: OperationSource,
    ) -> RiskAssessment:
        adjusted: list[KnowledgeOperation] = []
        warnings: list[str] = []
        operation_list = list(operations)

        for operation in operation_list:
            risk = max(operation.risk_level, self._base_risk(operation))
            paths = self._paths(operation)
            if any(self._is_protected(path) for path in paths):
                risk = RiskLevel.CRITICAL
                warnings.append(f"Protected path affected by {operation.operation_id}")
            if source.remote:
                risk = max(risk, self.config.remote_minimum_risk)
            if operation.operation_type is OperationType.INVOKE_PLUGIN:
                warnings.append(f"Plugin capability will be invoked by {operation.operation_id}")
            adjusted.append(replace(operation, risk_level=risk))

        count = len(adjusted)
        if count >= self.config.bulk_critical_threshold:
            adjusted = [replace(item, risk_level=RiskLevel.CRITICAL) for item in adjusted]
            warnings.append("Operation count exceeds critical bulk threshold")
        elif count >= self.config.bulk_high_threshold:
            adjusted = [replace(item, risk_level=max(item.risk_level, RiskLevel.HIGH)) for item in adjusted]
            warnings.append("Operation count exceeds high-risk bulk threshold")

        plan_risk = max((item.risk_level for item in adjusted), default=RiskLevel.LOW)
        if source.actor_type in {ActorType.AUTOMATION, ActorType.GATEWAY} and plan_risk >= RiskLevel.HIGH:
            warnings.append("Automated or remote high-risk plan requires local approval")
        return RiskAssessment(operations=tuple(adjusted), plan_risk=plan_risk, warnings=tuple(dict.fromkeys(warnings)))

    def _base_risk(self, operation: KnowledgeOperation) -> RiskLevel:
        if operation.operation_type is OperationType.CREATE_NOTE:
            policy = getattr(operation.input, "conflict_policy", ConflictPolicy.FAIL)
            return RiskLevel.CRITICAL if policy is ConflictPolicy.OVERWRITE else RiskLevel.LOW
        if operation.operation_type is OperationType.CREATE_TASK:
            return RiskLevel.LOW
        if operation.operation_type is OperationType.UPDATE_METADATA:
            return RiskLevel.MEDIUM
        if operation.operation_type is OperationType.MOVE_NOTE:
            value = operation.input
            assert isinstance(value, MoveNoteInput)
            return RiskLevel.HIGH if value.update_links else RiskLevel.MEDIUM
        if operation.operation_type is OperationType.UPDATE_NOTE:
            value = operation.input
            assert isinstance(value, UpdateNoteInput)
            return RiskLevel.HIGH if value.update_mode is UpdateMode.REPLACE_CONTENT else RiskLevel.MEDIUM
        if operation.operation_type is OperationType.INVOKE_PLUGIN:
            value = operation.input
            assert isinstance(value, InvokePluginInput)
            configured = value.parameters.get("declaredRisk")
            if configured == "critical":
                return RiskLevel.CRITICAL
            return RiskLevel.HIGH
        return RiskLevel.HIGH

    def _is_protected(self, path: VaultPath) -> bool:
        return any(
            path.value == protected.value or path.value.startswith(protected.value.rstrip("/") + "/")
            for protected in self.config.protected_paths
        )

    @staticmethod
    def _paths(operation: KnowledgeOperation) -> tuple[VaultPath, ...]:
        value = operation.input
        paths: list[VaultPath] = []
        for name in ("path", "target_path", "source_path", "source_note_path"):
            item = getattr(value, name, None)
            if isinstance(item, VaultPath):
                paths.append(item)
        return tuple(paths)
