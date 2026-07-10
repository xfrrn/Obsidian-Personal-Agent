from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from oka_domain.common import (
    AggregateRoot,
    NoteId,
    ProjectId,
    Sha256Hash,
    TagName,
    VaultPath,
    sha256_of,
    sha256_text,
    utc_now,
)
from oka_domain.exceptions import InvalidStateTransition, ValidationError
from .events import NoteArchived, NoteContentChanged, NoteCreated, NoteMetadataChanged, NoteMoved


class NoteStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    TRASHED = "trashed"


@dataclass(frozen=True, slots=True, kw_only=True)
class NoteHeading:
    level: int
    text: str
    line: int
    anchor: str | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.level <= 6:
            raise ValidationError("Heading level must be between 1 and 6")
        if not self.text.strip() or self.line < 0:
            raise ValidationError("Heading text cannot be blank and line must be non-negative")


@dataclass(frozen=True, slots=True, kw_only=True)
class NoteLink:
    target: str
    display_text: str | None = None
    heading: str | None = None
    block_id: str | None = None
    embedded: bool = False

    def __post_init__(self) -> None:
        if not self.target.strip():
            raise ValidationError("Link target cannot be blank")


@dataclass(slots=True, kw_only=True)
class NoteMetadata:
    note_type: str | None = None
    project_id: ProjectId | None = None
    status: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)

    def set_values(self, values: Mapping[str, Any]) -> None:
        self.properties.update(values)

    def remove_keys(self, keys: set[str] | frozenset[str]) -> None:
        for key in keys:
            self.properties.pop(key, None)


@dataclass(slots=True, kw_only=True)
class Note(AggregateRoot):
    note_id: NoteId
    path: VaultPath
    title: str
    body: str = ""
    metadata: NoteMetadata = field(default_factory=NoteMetadata)
    tags: set[TagName] = field(default_factory=set)
    links: tuple[NoteLink, ...] = ()
    headings: tuple[NoteHeading, ...] = ()
    status: NoteStatus = NoteStatus.ACTIVE
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    content_hash: Sha256Hash | None = None
    metadata_hash: Sha256Hash | None = None
    version_hash: Sha256Hash | None = None

    def __post_init__(self) -> None:
        self.title = self.title.strip()
        if not self.title:
            raise ValidationError("Note title cannot be blank")
        if not self.path.is_markdown:
            raise ValidationError("Note path must end with .md")
        self.recalculate_hashes()

    @classmethod
    def create(
        cls,
        *,
        path: VaultPath,
        title: str,
        body: str = "",
        metadata: NoteMetadata | None = None,
        tags: set[TagName] | None = None,
    ) -> Note:
        note = cls(
            note_id=NoteId.new(),
            path=path,
            title=title,
            body=body,
            metadata=metadata or NoteMetadata(),
            tags=tags or set(),
        )
        note._record(
            NoteCreated(
                aggregate_id=str(note.note_id),
                payload={"path": str(note.path), "title": note.title},
            )
        )
        return note

    def rename(self, title: str, *, path: VaultPath | None = None) -> None:
        title = title.strip()
        if not title:
            raise ValidationError("Note title cannot be blank")
        old_title = self.title
        old_path = self.path
        self.title = title
        if path is not None:
            if not path.is_markdown:
                raise ValidationError("Note path must end with .md")
            self.path = path
        self._touch()
        self._changed(
            NoteMoved(
                aggregate_id=str(self.note_id),
                payload={
                    "oldTitle": old_title,
                    "newTitle": self.title,
                    "oldPath": str(old_path),
                    "newPath": str(self.path),
                    "reason": "rename",
                },
            )
        )

    def move_to(self, path: VaultPath) -> None:
        if not path.is_markdown:
            raise ValidationError("Note path must end with .md")
        if path == self.path:
            return
        old_path = self.path
        self.path = path
        self._touch()
        self._changed(
            NoteMoved(
                aggregate_id=str(self.note_id),
                payload={"oldPath": str(old_path), "newPath": str(path), "reason": "move"},
            )
        )

    def replace_body(self, body: str, *, expected_content_hash: Sha256Hash | None = None) -> None:
        if expected_content_hash is not None and expected_content_hash != self.content_hash:
            raise InvalidStateTransition("Note content changed after the operation was planned")
        if body == self.body:
            return
        old_hash = self.content_hash
        self.body = body
        self._touch()
        self._changed(
            NoteContentChanged(
                aggregate_id=str(self.note_id),
                payload={"oldHash": str(old_hash), "newHash": str(self.content_hash)},
            )
        )

    def update_metadata(
        self,
        *,
        set_values: Mapping[str, Any] | None = None,
        remove_keys: set[str] | frozenset[str] = frozenset(),
        expected_metadata_hash: Sha256Hash | None = None,
    ) -> None:
        if expected_metadata_hash is not None and expected_metadata_hash != self.metadata_hash:
            raise InvalidStateTransition("Note metadata changed after the operation was planned")
        before = self.metadata_hash
        if set_values:
            self.metadata.set_values(set_values)
        self.metadata.remove_keys(remove_keys)
        self._touch()
        if before != self.metadata_hash:
            self._changed(
                NoteMetadataChanged(
                    aggregate_id=str(self.note_id),
                    payload={"oldHash": str(before), "newHash": str(self.metadata_hash)},
                )
            )

    def add_tags(self, *tags: TagName) -> None:
        before = set(self.tags)
        self.tags.update(tags)
        if before != self.tags:
            self._touch()
            self._changed(
                NoteMetadataChanged(
                    aggregate_id=str(self.note_id),
                    payload={"addedTags": sorted(str(tag) for tag in self.tags - before)},
                )
            )

    def remove_tags(self, *tags: TagName) -> None:
        before = set(self.tags)
        self.tags.difference_update(tags)
        if before != self.tags:
            self._touch()
            self._changed(
                NoteMetadataChanged(
                    aggregate_id=str(self.note_id),
                    payload={"removedTags": sorted(str(tag) for tag in before - self.tags)},
                )
            )

    def archive(self) -> None:
        if self.status is NoteStatus.TRASHED:
            raise InvalidStateTransition("A trashed note cannot be archived")
        if self.status is NoteStatus.ARCHIVED:
            return
        self.status = NoteStatus.ARCHIVED
        self._touch()
        self._changed(NoteArchived(aggregate_id=str(self.note_id), payload={"path": str(self.path)}))

    def mark_trashed(self) -> None:
        if self.status is NoteStatus.TRASHED:
            return
        self.status = NoteStatus.TRASHED
        self._touch()
        self.revision += 1

    def recalculate_hashes(self) -> None:
        self.content_hash = sha256_text(self.body)
        self.metadata_hash = sha256_of(
            {
                "metadata": self.metadata,
                "tags": self.tags,
                "status": self.status,
            }
        )
        self.version_hash = sha256_of(
            {
                "path": self.path,
                "title": self.title,
                "contentHash": self.content_hash,
                "metadataHash": self.metadata_hash,
            }
        )

    def _touch(self) -> None:
        self.updated_at = utc_now()
        self.recalculate_hashes()
