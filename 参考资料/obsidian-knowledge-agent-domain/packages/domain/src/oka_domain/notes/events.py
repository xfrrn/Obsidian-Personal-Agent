from dataclasses import dataclass
from typing import ClassVar

from oka_domain.common.events import DomainEvent


@dataclass(frozen=True, slots=True, kw_only=True)
class NoteCreated(DomainEvent):
    event_type: ClassVar[str] = "note.created"


@dataclass(frozen=True, slots=True, kw_only=True)
class NoteContentChanged(DomainEvent):
    event_type: ClassVar[str] = "note.content-changed"


@dataclass(frozen=True, slots=True, kw_only=True)
class NoteMoved(DomainEvent):
    event_type: ClassVar[str] = "note.moved"


@dataclass(frozen=True, slots=True, kw_only=True)
class NoteMetadataChanged(DomainEvent):
    event_type: ClassVar[str] = "note.metadata-changed"


@dataclass(frozen=True, slots=True, kw_only=True)
class NoteArchived(DomainEvent):
    event_type: ClassVar[str] = "note.archived"
