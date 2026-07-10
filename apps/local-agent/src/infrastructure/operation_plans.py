"""Minimal OperationPlan adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence
from uuid import uuid4


@dataclass
class SimpleOperationPlanner:
    """Build a draft plan from already-resolved write requests."""

    async def build(
        self,
        requested_operations: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return {
            "id": f"op_{uuid4().hex}",
            "schemaVersion": "1.0",
            "type": "operation-plan",
            "summary": "准备修改知识库",
            "risk": "low",
            "requiresConfirmation": True,
            "requestedOperations": tuple(requested_operations),
            "context": dict(context),
            "createdAt": datetime.now(UTC).isoformat(),
        }


@dataclass
class InMemoryOperationPlanStore:
    """Store pending plans in process memory."""

    plans: dict[str, Mapping[str, Any]] = field(default_factory=dict)

    async def save(self, plan: Mapping[str, Any]) -> Mapping[str, Any]:
        plan_id = str(plan.get("id") or f"op_{uuid4().hex}")
        stored = {**dict(plan), "id": plan_id}
        self.plans[plan_id] = stored
        return stored

    async def get(self, operation_plan_id: str) -> Mapping[str, Any]:
        try:
            return self.plans[operation_plan_id]
        except KeyError as exc:
            raise KeyError(f"unknown operation plan: {operation_plan_id}") from exc


@dataclass
class NoopOperationPlanExecutor:
    """Refuse real writes until an Obsidian/file executor is wired."""

    async def execute(self, plan: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        # ponytail: no real write executor yet; replace when preview/confirm UI is connected.
        return ({"status": "skipped", "reason": "operation executor is not wired", "planId": plan["id"]},)
