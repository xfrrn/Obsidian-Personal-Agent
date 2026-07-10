from dataclasses import dataclass
from typing import ClassVar

from oka_domain.common.events import DomainEvent


@dataclass(frozen=True, slots=True, kw_only=True)
class TagCreated(DomainEvent):
    event_type: ClassVar[str] = "tag.created"


@dataclass(frozen=True, slots=True, kw_only=True)
class TagDeprecated(DomainEvent):
    event_type: ClassVar[str] = "tag.deprecated"


@dataclass(frozen=True, slots=True, kw_only=True)
class TagMerged(DomainEvent):
    event_type: ClassVar[str] = "tag.merged"


@dataclass(frozen=True, slots=True, kw_only=True)
class TagAliasAdded(DomainEvent):
    event_type: ClassVar[str] = "tag.alias-added"
