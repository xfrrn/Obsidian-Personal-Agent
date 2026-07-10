from __future__ import annotations

from dataclasses import dataclass, field

from .events import DomainEvent


@dataclass(slots=True, kw_only=True)
class AggregateRoot:
    revision: int = 0
    _domain_events: list[DomainEvent] = field(default_factory=list, init=False, repr=False)

    def _record(self, event: DomainEvent) -> None:
        self._domain_events.append(event)

    def _changed(self, event: DomainEvent) -> None:
        self.revision += 1
        self._record(event)

    def pull_domain_events(self) -> tuple[DomainEvent, ...]:
        events = tuple(self._domain_events)
        self._domain_events.clear()
        return events

    def peek_domain_events(self) -> tuple[DomainEvent, ...]:
        return tuple(self._domain_events)
