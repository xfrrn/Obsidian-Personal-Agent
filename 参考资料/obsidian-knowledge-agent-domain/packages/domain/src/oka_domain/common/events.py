from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar, Mapping

from .value_objects import EventId


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True, kw_only=True)
class DomainEvent:
    aggregate_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=utc_now)
    event_id: EventId = field(default_factory=EventId.new)

    event_type: ClassVar[str] = "domain.event"
