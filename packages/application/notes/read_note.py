"""Read note use case."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from domain.notes.repositories import NoteRepository


@dataclass(frozen=True)
class ReadNoteUseCase:
    notes: NoteRepository

    async def execute(self, input_data: Mapping[str, Any]) -> dict[str, Any]:
        path = _note_path(input_data)
        return {"note": await self.notes.get(path)}


def _note_path(input_data: Mapping[str, Any]) -> str:
    for key in ("path", "notePath", "activeFilePath"):
        value = input_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError("note path is required")
