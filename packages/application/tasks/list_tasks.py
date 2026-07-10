"""List tasks use case."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from domain.tasks.repositories import TaskRepository


@dataclass(frozen=True)
class ListTasksUseCase:
    tasks: TaskRepository

    async def execute(self, input_data: Mapping[str, Any]) -> dict[str, Any]:
        query = _text(input_data.get("query")) or _text(input_data.get("keyword")) or _text(input_data.get("rawText"))
        status = _text(input_data.get("status")) or None
        limit = int(input_data.get("limit", 50))
        tasks = tuple(await self.tasks.list(query, status=status, limit=limit))
        return {"query": query, "status": status, "count": len(tasks), "tasks": tasks}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
