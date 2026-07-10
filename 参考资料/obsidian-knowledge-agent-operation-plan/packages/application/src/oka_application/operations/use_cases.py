from __future__ import annotations

from oka_domain.common import Identifier, PlanId
from oka_domain.operations import ApproverType, OperationPlan, OperationPlanRepository

from .builder import OperationPlanBuilder
from .confirmation import ConfirmationService
from .coordinator import ExecutionCoordinator
from .models import ExecutionContext, OperationExecution, OperationPlanPreview
from .preview import DefaultPlanPreviewService


class SaveOperationPlanUseCase:
    def __init__(self, plans: OperationPlanRepository) -> None:
        self.plans = plans

    async def execute(self, plan: OperationPlan) -> OperationPlan:
        existing = await self.plans.get_by_idempotency_key(plan.idempotency_key)
        if existing is not None:
            return existing
        await self.plans.save(plan)
        return plan


class PreviewOperationPlanUseCase:
    def __init__(
        self,
        plans: OperationPlanRepository,
        preview: DefaultPlanPreviewService,
    ) -> None:
        self.plans = plans
        self.preview = preview

    async def execute(self, plan_id: PlanId, context: ExecutionContext) -> OperationPlanPreview:
        plan = await self.plans.get(plan_id)
        if plan is None:
            raise LookupError("Operation plan not found")
        return await self.preview.build(plan, context)


class ConfirmOperationPlanUseCase:
    def __init__(self, service: ConfirmationService) -> None:
        self.service = service

    async def execute(
        self,
        *,
        plan_id: PlanId,
        plan_version: int,
        approver_type: ApproverType,
        approver_id: Identifier,
    ) -> str:
        return await self.service.confirm(
            plan_id=plan_id,
            plan_version=plan_version,
            approver_type=approver_type,
            approver_id=approver_id,
        )


class ExecuteOperationPlanUseCase:
    def __init__(self, coordinator: ExecutionCoordinator) -> None:
        self.coordinator = coordinator

    async def execute(
        self,
        *,
        plan_id: PlanId,
        plan_version: int,
        context: ExecutionContext,
        confirmation_token: str | None = None,
    ) -> OperationExecution:
        return await self.coordinator.execute(
            plan_id=plan_id,
            plan_version=plan_version,
            context=context,
            confirmation_token=confirmation_token,
        )
