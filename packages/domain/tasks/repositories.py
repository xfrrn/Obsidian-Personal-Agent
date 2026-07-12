"""Task repository ports."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence


class TaskRepository(Protocol):
    """Port for querying task-like items from notes."""

    async def list(
        self,
        query: str = "",
        *,
        status: str | None = None,
        limit: int = 50,
        path: str | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        """Return tasks filtered by text and optional status."""
        ...
