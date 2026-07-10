"""Search notes use case."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from domain.notes.repositories import NoteRepository


@dataclass(frozen=True)
class SearchNotesUseCase:
    notes: NoteRepository

    async def execute(self, input_data: Mapping[str, Any]) -> dict[str, Any]:
        query = _first_text(input_data, "query", "keyword", "projectName", "noteName", "rawText")
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
        limit = int(input_data.get("limit", 10))
        results = tuple(await self.notes.search(query, limit=limit))
        return {"query": query, "count": len(results), "results": results}


def _first_text(input_data: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = input_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
