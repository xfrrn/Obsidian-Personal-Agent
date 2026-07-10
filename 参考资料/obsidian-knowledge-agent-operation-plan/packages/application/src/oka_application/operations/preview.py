from __future__ import annotations

from oka_domain.operations import OperationPlan, OperationType

from .models import ExecutionContext, OperationPlanPreview
from .registry import OperationHandlerRegistry


class DefaultPlanPreviewService:
    def __init__(self, registry: OperationHandlerRegistry) -> None:
        self.registry = registry

    async def build(self, plan: OperationPlan, context: ExecutionContext) -> OperationPlanPreview:
        created = []
        updated = []
        moved = []
        plugins = []
        diffs = []
        for operation in plan.operations:
            value = operation.input
            if operation.operation_type in {OperationType.CREATE_NOTE, OperationType.CREATE_TASK}:
                created.append(value.path if hasattr(value, "path") else value.target_path)
            elif operation.operation_type in {OperationType.UPDATE_NOTE, OperationType.UPDATE_METADATA}:
                updated.append(value.path)
            elif operation.operation_type is OperationType.MOVE_NOTE:
                moved.append((value.source_path, value.target_path))
            elif operation.operation_type is OperationType.INVOKE_PLUGIN:
                plugins.append(f"{value.plugin_id}:{value.capability}")
            diff = await self.registry.resolve(operation.operation_type).preview(operation, context)
            if diff is not None:
                diffs.append(diff)
        distinct_paths = {
            str(path)
            for path in created + updated + [item for pair in moved for item in pair]
        }
        return OperationPlanPreview(
            plan_id=plan.plan_id,
            plan_version=plan.plan_version,
            summary=plan.summary,
            risk_level=plan.risk_level,
            affected_file_count=len(distinct_paths),
            created_files=tuple(created),
            updated_files=tuple(updated),
            moved_files=tuple(moved),
            plugin_invocations=tuple(plugins),
            diffs=tuple(diffs),
            warnings=tuple(item.message for item in plan.warnings),
            rollback_available=plan.rollback_policy.mode.value != "none",
        )
