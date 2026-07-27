"""Read note use case."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .ports import NoteRepository


@dataclass(frozen=True)
class ReadNoteUseCase:
    notes: NoteRepository

    async def execute(self, input_data: Mapping[str, Any]) -> dict[str, Any]:
        paths = _note_paths(input_data)
        notes: list[dict[str, Any]] = []
        remaining = 50_000
        for path in paths:
            note = dict(await self.notes.get(path))
            content = note.get("content")
            if isinstance(content, str) and len(content) > remaining:
                note["content"] = content[: max(remaining, 0)]
                note["truncated"] = True
            remaining -= len(str(note.get("content", "")))
            notes.append(note)
            if remaining <= 0:
                break
        result: dict[str, Any] = {"count": len(notes), "notes": tuple(notes)}
        if len(notes) == 1:
            result["note"] = notes[0]
        return result


def _note_paths(input_data: Mapping[str, Any]) -> tuple[str, ...]:
    values = input_data.get("paths")
    if values is not None:
        if not isinstance(values, (list, tuple)) or not values:
            raise ValueError("paths must be a non-empty list")
        if len(values) > 8:
            raise ValueError("at most 8 notes can be read at once")
        paths = tuple(str(value).strip() for value in values if isinstance(value, str) and value.strip())
        if len(paths) != len(values):
            raise ValueError("every note path must be a non-empty string")
        return tuple(dict.fromkeys(paths))
    for key in ("path", "notePath", "activeFilePath"):
        value = input_data.get(key)
        if isinstance(value, str) and value.strip():
            return (value.strip(),)
    raise ValueError("note path is required")
