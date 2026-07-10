from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence

from oka_domain.common import NoteId, ProjectId, TagName, VaultPath
from .models import Note, NoteStatus


@dataclass(frozen=True, slots=True, kw_only=True)
class NoteQuery:
    folders: tuple[VaultPath, ...] = ()
    project_ids: tuple[ProjectId, ...] = ()
    tags: tuple[TagName, ...] = ()
    note_types: tuple[str, ...] = ()
    statuses: tuple[NoteStatus, ...] = ()
    updated_after: datetime | None = None
    limit: int = 100


class NoteRepository(Protocol):
    async def get(self, note_id: NoteId) -> Note | None: ...

    async def get_by_path(self, path: VaultPath) -> Note | None: ...

    async def list(self, query: NoteQuery) -> Sequence[Note]: ...

    async def save(self, note: Note) -> None: ...

    async def delete(self, note_id: NoteId) -> None: ...
