from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from oka_domain.common import AggregateRoot, TagId, TagName, utc_now
from oka_domain.exceptions import InvalidStateTransition, ValidationError
from .events import TagAliasAdded, TagCreated, TagDeprecated, TagMerged


class TagCategory(StrEnum):
    TYPE = "type"
    DOMAIN = "domain"
    PROJECT = "project"
    STATUS = "status"
    SOURCE = "source"
    CUSTOM = "custom"


class TagStatus(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    MERGED = "merged"


class TagScope(StrEnum):
    GLOBAL = "global"
    NOTE = "note"
    TASK = "task"
    PROJECT = "project"


@dataclass(slots=True, kw_only=True)
class Tag(AggregateRoot):
    tag_id: TagId
    name: TagName
    category: TagCategory = TagCategory.CUSTOM
    description: str = ""
    aliases: set[TagName] = field(default_factory=set)
    scopes: set[TagScope] = field(default_factory=lambda: {TagScope.GLOBAL})
    parent_tag_id: TagId | None = None
    status: TagStatus = TagStatus.ACTIVE
    replacement_tag_id: TagId | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.parent_tag_id == self.tag_id:
            raise ValidationError("Tag cannot be its own parent")
        if self.name in self.aliases:
            raise ValidationError("Canonical tag name cannot also be an alias")
        if self.status is TagStatus.MERGED and self.replacement_tag_id is None:
            raise ValidationError("Merged tag must point to a replacement tag")

    @classmethod
    def create(
        cls,
        *,
        name: TagName,
        category: TagCategory = TagCategory.CUSTOM,
        description: str = "",
        scopes: set[TagScope] | None = None,
    ) -> Tag:
        tag = cls(
            tag_id=TagId.new(),
            name=name,
            category=category,
            description=description,
            scopes=scopes or {TagScope.GLOBAL},
        )
        tag._record(
            TagCreated(
                aggregate_id=str(tag.tag_id),
                payload={"name": str(tag.name), "category": tag.category.value},
            )
        )
        return tag

    def add_alias(self, alias: TagName) -> None:
        if self.status is not TagStatus.ACTIVE:
            raise InvalidStateTransition("Only active tags can receive aliases")
        if alias == self.name:
            raise ValidationError("Alias cannot equal canonical tag name")
        if alias not in self.aliases:
            self.aliases.add(alias)
            self._touch()
            self._changed(
                TagAliasAdded(
                    aggregate_id=str(self.tag_id), payload={"alias": str(alias)}
                )
            )

    def deprecate(self) -> None:
        if self.status is TagStatus.MERGED:
            raise InvalidStateTransition("Merged tag cannot be deprecated separately")
        if self.status is TagStatus.DEPRECATED:
            return
        self.status = TagStatus.DEPRECATED
        self._touch()
        self._changed(TagDeprecated(aggregate_id=str(self.tag_id), payload={"name": str(self.name)}))

    def merge_into(self, replacement_tag_id: TagId) -> None:
        if replacement_tag_id == self.tag_id:
            raise ValidationError("Tag cannot be merged into itself")
        self.status = TagStatus.MERGED
        self.replacement_tag_id = replacement_tag_id
        self._touch()
        self._changed(
            TagMerged(
                aggregate_id=str(self.tag_id),
                payload={"replacementTagId": str(replacement_tag_id)},
            )
        )

    def _touch(self) -> None:
        self.updated_at = utc_now()
