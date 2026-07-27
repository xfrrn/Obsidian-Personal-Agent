"""Minimal repository ports used by the Obsidian tools."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence


class NoteRepository(Protocol):
    async def get(self, path: str) -> Mapping[str, Any]: ...

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        filters: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]: ...

    async def all(self, *, limit: int = 5000) -> Sequence[Mapping[str, Any]]: ...

    async def rules(self) -> Sequence[Mapping[str, Any]]: ...


class TaskRepository(Protocol):
    async def list(
        self,
        query: str = "",
        *,
        status: str | None = None,
        limit: int = 50,
        path: str | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]: ...


class OperationPlanner(Protocol):
    async def build(
        self,
        requested_operations: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class OperationPlanStore(Protocol):
    async def save(self, plan: Mapping[str, Any]) -> Mapping[str, Any]: ...

    async def get(self, operation_plan_id: str) -> Mapping[str, Any]: ...
