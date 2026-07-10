"""List tasks use case."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from domain.tasks.repositories import TaskRepository


@dataclass(frozen=True)
class ListTasksUseCase:
    tasks: TaskRepository

    async def execute(self, input_data: Mapping[str, Any]) -> dict[str, Any]:
        query = _text(input_data.get("query")) or _text(input_data.get("keyword"))
        raw_text = _text(input_data.get("rawText"))
        status = _text(input_data.get("status")) or _status_from_text(raw_text)
        limit = int(input_data.get("limit", 50))
        path = _text(input_data.get("activeFilePath")) if input_data.get("scope") == "current" else None
        if input_data.get("scope") == "current" and not path:
            raise ValueError("activeFilePath is required for current scope")
        tasks = tuple(await self.tasks.list(query, status=status, limit=limit, path=path))
        return {"query": query, "status": status, "count": len(tasks), "tasks": tasks}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _status_from_text(value: str) -> str | None:
    if any(marker in value.casefold() for marker in ("已完成", "完成了", "done", "completed")):
        return "completed"
    if any(marker in value.casefold() for marker in ("未完成", "待办", "todo", "pending")):
        return "open"
    return None
