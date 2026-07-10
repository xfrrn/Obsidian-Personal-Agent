from __future__ import annotations

from typing import Protocol, Sequence

from oka_domain.common import IdempotencyKey, PlanId
from .models import OperationPlan, PlanStatus


class OperationPlanRepository(Protocol):
    async def get(self, plan_id: PlanId) -> OperationPlan | None: ...

    async def get_by_idempotency_key(self, key: IdempotencyKey) -> OperationPlan | None: ...

    async def list_by_status(self, statuses: Sequence[PlanStatus]) -> Sequence[OperationPlan]: ...

    async def save(self, plan: OperationPlan) -> None: ...
