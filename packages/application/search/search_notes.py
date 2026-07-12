"""Search notes use case."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Any, Mapping

from domain.notes.repositories import NoteRepository


@dataclass(frozen=True)
class SearchNotesUseCase:
    notes: NoteRepository

    async def execute(self, input_data: Mapping[str, Any]) -> dict[str, Any]:
        query = _first_text(input_data, "query", "keyword", "noteName", "rawText")
        if input_data.get("scope") == "current":
            path = _first_text(input_data, "activeFilePath")
            if not path:
                raise ValueError("activeFilePath is required for current scope")
            note = await self.notes.get(path)
            content = note.get("content", "")
            result = {
                "path": note.get("path", path),
                "title": note.get("title"),
                "excerpt": " ".join(content.split())[:500] if isinstance(content, str) else "",
            }
            return {"query": query, "count": 1, "results": (result,)}
        limit = _limit(input_data.get("limit", 10))
        filters = {
            key: input_data[key]
            for key in (
                "path_prefix",
                "path",
                "tags",
                "project",
                "modified_after",
                "modified_before",
                "sort",
            )
            if input_data.get(key) not in (None, "", [], ())
        }
        project_name = _first_text(input_data, "projectName")
        if project_name:
            filters.setdefault("project", project_name)
        if tags := _entity_values(input_data, "tag"):
            filters.setdefault("tags", tags)
        if folders := _entity_values(input_data, "folder"):
            filters.setdefault("path_prefix", folders[0])
        for key, value in _date_filters(_first_text(input_data, "rawText")).items():
            filters.setdefault(key, value)
        results = tuple(await self.notes.search(query, limit=limit, filters=filters))
        return {"query": query, "filters": filters, "count": len(results), "results": results}


def _first_text(input_data: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = input_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _limit(value: Any) -> int:
    try:
        return max(1, min(int(value), 100))
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc


def _entity_values(input_data: Mapping[str, Any], entity_type: str) -> list[str]:
    entities = input_data.get("entities")
    if not isinstance(entities, (list, tuple)):
        return []
    return [
        str(item["value"]).strip()
        for item in entities
        if isinstance(item, Mapping) and item.get("type") == entity_type and isinstance(item.get("value"), str)
    ]


def _date_filters(text: str) -> dict[str, str]:
    now = datetime.now().astimezone()
    if "最近一周" in text:
        return {"modified_after": (now - timedelta(days=7)).isoformat()}
    if "今天" in text:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return {"modified_after": start.isoformat(), "modified_before": (start + timedelta(days=1)).isoformat()}
    if "本周" in text:
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        return {"modified_after": start.isoformat(), "modified_before": (start + timedelta(days=7)).isoformat()}
    if "这个月" in text or "本月" in text:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return {"modified_after": start.isoformat(), "modified_before": next_month.isoformat()}
    if match := re.search(r"(\d{4}-\d{2}-\d{2})", text):
        day = datetime.fromisoformat(match.group(1)).replace(tzinfo=now.tzinfo)
        if any(marker in text for marker in ("以来", "之后", "以后")):
            return {"modified_after": day.isoformat()}
        if any(marker in text for marker in ("之前", "以前")):
            return {"modified_before": day.isoformat()}
        return {"modified_after": day.isoformat(), "modified_before": (day + timedelta(days=1)).isoformat()}
    return {}
