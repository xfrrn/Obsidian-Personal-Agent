"""Search notes use case."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from domain.notes.repositories import NoteRepository


@dataclass(frozen=True)
class SearchNotesUseCase:
    notes: NoteRepository

    async def execute(self, input_data: Mapping[str, Any]) -> dict[str, Any]:
        query = _first_text(input_data, "query", "keyword", "rawText")
        limit = int(input_data.get("limit", 10))
        results = tuple(await self.notes.search(query, limit=limit))
        return {"query": query, "count": len(results), "results": results}


def _first_text(input_data: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = input_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
