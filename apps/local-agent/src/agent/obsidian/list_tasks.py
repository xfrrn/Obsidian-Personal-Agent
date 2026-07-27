"""List tasks use case."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Mapping

from .ports import TaskRepository


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
        context_tasks = _context_tasks(input_data.get("contextTasks"))
        if context_tasks is None:
            tasks = tuple(await self.tasks.list(
                query,
                status=status,
                limit=limit,
                path=path,
                filters=filters,
            ))
        else:
            tasks = _filter_context_tasks(context_tasks, query, status, limit, path, filters)
        return {"query": query, "status": status, "filters": filters, "count": len(tasks), "tasks": tasks}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _status_from_text(value: str) -> str | None:
    if any(marker in value.casefold() for marker in ("已完成", "完成了", "done", "completed")):
        return "completed"
    if any(marker in value.casefold() for marker in ("未完成", "没有完成", "没完成", "还没完成", "待办", "逾期", "todo", "pending")):
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


def _completed_filter(status: str | None) -> bool | None:
    if not status:
        return None
    value = status.casefold()
    if value in {"done", "completed", "complete", "closed", "已完成"}:
        return True
    if value in {"open", "todo", "pending", "未完成"}:
        return False
    return None


def _limit(value: Any) -> int:
    try:
        return max(1, min(int(value), 500))
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc


def _context_tasks(value: Any) -> tuple[Mapping[str, Any], ...] | None:
    if not isinstance(value, (list, tuple)):
        return None
    tasks: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        path = _text(item.get("path"))
        title = _text(item.get("title"))
        line = _line(item.get("line"))
        completed = item.get("completed")
        if not path or not title or line is None or not isinstance(completed, bool):
            continue
        task = dict(item)
        task["path"] = path
        task["line"] = line
        task["title"] = title
        task["completed"] = completed
        if "due" not in task and _text(task.get("dueDate")):
            task["due"] = _text(task.get("dueDate"))
        tasks.append(task)
    return tuple(tasks)


def _filter_context_tasks(
    tasks: tuple[Mapping[str, Any], ...],
    query: str,
    status: str | None,
    limit: int,
    path: str | None,
    filters: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    query_text = query.casefold().strip()
    want_completed = _completed_filter(status)
    path_prefix = str(filters.get("path_prefix") or "").replace("\\", "/").strip("/")
    result: list[Mapping[str, Any]] = []
    for task in tasks:
        task_path = str(task["path"])
        if path and task_path != path:
            continue
        if path_prefix and task_path != path_prefix and not task_path.startswith(path_prefix + "/"):
            continue
        if want_completed is not None and task["completed"] is not want_completed:
            continue
        if query_text and query_text not in f"{task_path} {task['title']} {task.get('heading') or ''}".casefold():
            continue
        if not _matches_task(task, filters):
            continue
        result.append(task)
    result.sort(key=lambda item: (str(item.get("due") or "9999-99-99"), str(item["path"]), int(item["line"])))
    return tuple(result[:limit])


def _line(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _matches_task(task: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    for key in ("priority", "project"):
        value = filters.get(key)
        if isinstance(value, str) and value.strip() and str(task.get(key) or "").casefold() != value.strip().casefold():
            return False
    due = task.get("due")
    if filters.get("due_on") and due != filters["due_on"]:
        return False
    if filters.get("due_after") and (not due or due < filters["due_after"]):
        return False
    if filters.get("due_before") and (not due or due >= filters["due_before"]):
        return False
    return True
