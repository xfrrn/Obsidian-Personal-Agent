from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from oka_domain.common import TagId, TagName
from .models import Tag, TagCategory, TagStatus


@dataclass(frozen=True, slots=True, kw_only=True)
class TagQuery:
    names: tuple[TagName, ...] = ()
    categories: tuple[TagCategory, ...] = ()
    statuses: tuple[TagStatus, ...] = ()
    limit: int = 200


class TagRepository(Protocol):
    async def get(self, tag_id: TagId) -> Tag | None: ...

    async def get_by_name_or_alias(self, name: TagName) -> Tag | None: ...

    async def list(self, query: TagQuery) -> Sequence[Tag]: ...

    async def save(self, tag: Tag) -> None: ...

    async def delete(self, tag_id: TagId) -> None: ...
