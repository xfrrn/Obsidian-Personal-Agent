from __future__ import annotations

from datetime import datetime

from oka_domain.common import utc_now
from oka_domain.exceptions import DomainError
from oka_domain.operations import ApproverType, OperationPlan, PlanStatus, RiskLevel

from .errors import OperationSafetyError
from .models import ExecutionContext
from .ports import PermissionAuthorizer, RuntimeInspector
from .registry import OperationHandlerRegistry


class OperationPlanValidator:
    def __init__(
        self,
        *,
        authorizer: PermissionAuthorizer,
        inspector: RuntimeInspector,
        registry: OperationHandlerRegistry,
    ) -> None:
        self.authorizer = authorizer
        self.inspector = inspector
        self.registry = registry

    async def validate_for_storage(self, plan: OperationPlan) -> None:
        try:
            plan.validate_structure()
            plan.validate_integrity()
        except DomainError as exc:
            raise OperationSafetyError(str(exc)) from exc
        for operation in plan.operations:
            self.registry.resolve(operation.operation_type)
        if plan.risk_level is RiskLevel.CRITICAL:
            policy = plan.confirmation_policy
            if policy is None or ApproverType.LOCAL_USER not in policy.approver_types:
                raise OperationSafetyError("Critical plan must permit a local-user approver")

    async def validate_for_execution(
        self,
        plan: OperationPlan,
        context: ExecutionContext,
        *,
        now: datetime | None = None,
    ) -> None:
        await self.validate_for_storage(plan)
        now = now or utc_now()
        if plan.status is not PlanStatus.CONFIRMED:
            raise OperationSafetyError(
                "Only confirmed plans can execute",
                details={"status": plan.status.value},
            )
        if plan.expires_at is not None and now >= plan.expires_at:
            raise OperationSafetyError("Operation plan has expired")
        await self.authorizer.authorize_plan(plan, context)
        approved = plan.approved_operations()
        if plan.requires_confirmation and not approved:
            raise OperationSafetyError("No operations were approved")
        for operation in approved:
            self.registry.resolve(operation.operation_type)
            await self.authorizer.authorize_operation(operation, context)
            for precondition in operation.preconditions:
                await self.inspector.check_precondition(precondition, context)
