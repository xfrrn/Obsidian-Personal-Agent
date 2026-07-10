from __future__ import annotations

from datetime import timedelta
from typing import Sequence
import secrets

from oka_domain.common import Identifier, OperationId, PlanId, utc_now
from oka_domain.operations import ApproverType, OperationPlanRepository, PlanStatus

from .errors import OperationConfirmationError
from .models import ConfirmationClaims
from .ports import ConfirmationTokenService
from .validator import OperationPlanValidator


class ConfirmationService:
    def __init__(
        self,
        *,
        plans: OperationPlanRepository,
        validator: OperationPlanValidator,
        tokens: ConfirmationTokenService,
        token_ttl_seconds: int = 600,
    ) -> None:
        self.plans = plans
        self.validator = validator
        self.tokens = tokens
        self.token_ttl_seconds = token_ttl_seconds

    async def confirm(
        self,
        *,
        plan_id: PlanId,
        plan_version: int,
        approver_type: ApproverType,
        approver_id: Identifier,
        approved_operation_ids: Sequence[OperationId] | None = None,
    ) -> str:
        plan = await self.plans.get(plan_id)
        if plan is None:
            raise OperationConfirmationError("Operation plan not found")
        if plan.plan_version != plan_version:
            raise OperationConfirmationError("Operation plan version mismatch")
        await self.validator.validate_for_storage(plan)
        if plan.status is PlanStatus.CONFIRMED and not plan.requires_confirmation:
            approved = tuple(item.operation_id for item in plan.operations)
        else:
            try:
                plan.confirm(
                    approver_type=approver_type,
                    approver_id=approver_id,
                    approved_operation_ids=approved_operation_ids,
                )
            except Exception as exc:
                raise OperationConfirmationError(str(exc)) from exc
            await self.plans.save(plan)
            approved = tuple(item.operation_id for item in plan.approved_operations())

        now = utc_now()
        claims = ConfirmationClaims(
            plan_id=plan.plan_id,
            plan_version=plan.plan_version,
            integrity_hash=plan.integrity,
            approver_id=approver_id,
            approved_operation_ids=approved,
            issued_at=now,
            expires_at=now + timedelta(seconds=self.token_ttl_seconds),
            nonce=secrets.token_urlsafe(24),
        )
        return self.tokens.issue(claims)
