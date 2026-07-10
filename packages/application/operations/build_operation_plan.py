"""Build OperationPlan use case."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from domain.operations.repositories import OperationPlanStore, OperationPlanner


@dataclass(frozen=True)
class BuildOperationPlanUseCase:
    planner: OperationPlanner
    store: OperationPlanStore

    async def execute(self, input_data: Mapping[str, Any]) -> dict[str, Any]:
        requested = input_data.get("requestedOperations", ())
        if not isinstance(requested, (list, tuple)):
            raise ValueError("requestedOperations must be a list")
        context = input_data.get("context", {})
        if not isinstance(context, Mapping):
            raise ValueError("context must be an object")

        plan = await self.planner.build(tuple(requested), context)
        stored = await self.store.save(plan)
        plan_id = stored.get("id") or stored.get("operationPlanId")
        return {
            "message": "已生成操作计划，等待确认。",
            "operationPlanId": plan_id,
            "plan": stored,
        }
