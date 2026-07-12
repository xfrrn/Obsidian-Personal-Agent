"""Operation plan ports."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence


class OperationPlanner(Protocol):
    """Port for turning write requests into a safe OperationPlan draft."""

    async def build(
        self,
        requested_operations: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Return an OperationPlan-shaped mapping."""
        ...


class OperationPlanStore(Protocol):
    """Port for saving and loading pending OperationPlans."""

    async def save(self, plan: Mapping[str, Any]) -> Mapping[str, Any]:
        """Persist a pending plan and return the stored plan."""
        ...

    async def get(self, operation_plan_id: str) -> Mapping[str, Any]:
        """Load one pending plan by id."""
        ...


class OperationPlanExecutor(Protocol):
    """Port for executing a confirmed OperationPlan."""

    async def execute(self, plan: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        """Execute a confirmed plan and return operation results."""
        ...

    async def rollback(self, plan: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        """Restore a succeeded plan after verifying the current file versions."""
        ...
