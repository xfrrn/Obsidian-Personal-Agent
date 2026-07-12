"""Rollback one succeeded OperationPlan."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from domain.operations.repositories import OperationPlanExecutor, OperationPlanStore


@dataclass(frozen=True)
class RollbackOperationUseCase:
    store: OperationPlanStore
    executor: OperationPlanExecutor

    async def execute(self, input_data: Mapping[str, Any]) -> dict[str, Any]:
        plan_id = input_data.get("operationPlanId")
        if not isinstance(plan_id, str) or not plan_id.strip():
            raise ValueError("operationPlanId is required")
        plan = await self.store.get(plan_id.strip())
        results = tuple(await self.executor.rollback(plan))
        return {
            "message": f"已撤销操作计划：{plan_id}",
            "operationPlanId": plan_id,
            "results": results,
        }
