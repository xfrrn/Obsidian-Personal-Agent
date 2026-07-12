"""List tasks use case."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Mapping

from domain.tasks.repositories import TaskRepository


@dataclass(frozen=True)
class ListTasksUseCase:
    tasks: TaskRepository

    async def execute(self, input_data: Mapping[str, Any]) -> dict[str, Any]:
        query = _text(input_data.get("query")) or _text(input_data.get("keyword"))
        raw_text = _text(input_data.get("rawText"))
        status = _text(input_data.get("status")) or _status_from_text(raw_text)
        limit = _limit(input_data.get("limit", 50))
        path = _text(input_data.get("activeFilePath")) if input_data.get("scope") == "current" else None
        if input_data.get("scope") == "current" and not path:
            raise ValueError("activeFilePath is required for current scope")
        filters = _date_filters(raw_text)
        for key in ("due_on", "due_after", "due_before", "priority", "project", "path_prefix"):
            value = input_data.get(key)
            if isinstance(value, str) and value.strip():
                filters[key] = value.strip()
        if project := _text(input_data.get("projectName")):
            filters.setdefault("project", project)
        if priority := _priority_from_text(raw_text):
            filters.setdefault("priority", priority)
        tasks = tuple(await self.tasks.list(
            query,
            status=status,
            limit=limit,
            path=path,
            filters=filters,
        ))
        return {"query": query, "status": status, "filters": filters, "count": len(tasks), "tasks": tasks}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _status_from_text(value: str) -> str | None:
    if any(marker in value.casefold() for marker in ("已完成", "完成了", "done", "completed")):
        return "completed"
    if any(marker in value.casefold() for marker in ("未完成", "待办", "逾期", "todo", "pending")):
        return "open"
    return None


def _date_filters(value: str) -> dict[str, str]:
    today = date.today()
    if "逾期" in value:
        return {"due_before": today.isoformat()}
    if "今天" in value:
        return {"due_on": today.isoformat()}
    if "明天" in value:
        return {"due_on": (today + timedelta(days=1)).isoformat()}
    if "本周" in value:
        return {
            "due_after": today.isoformat(),
            "due_before": (today + timedelta(days=7 - today.weekday())).isoformat(),
        }
    return {}


def _priority_from_text(value: str) -> str | None:
    for name, markers in {
        "highest": ("最高优先级", "紧急"),
        "high": ("高优先级",),
        "medium": ("中优先级",),
        "low": ("低优先级",),
        "lowest": ("最低优先级",),
    }.items():
        if any(marker in value for marker in markers):
            return name
    return None


def _limit(value: Any) -> int:
    try:
        return max(1, min(int(value), 500))
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
