from dataclasses import dataclass
from typing import ClassVar

from oka_domain.common.events import DomainEvent


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskCreated(DomainEvent):
    event_type: ClassVar[str] = "task.created"


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskStatusChanged(DomainEvent):
    event_type: ClassVar[str] = "task.status-changed"


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskRescheduled(DomainEvent):
    event_type: ClassVar[str] = "task.rescheduled"


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskProjectChanged(DomainEvent):
    event_type: ClassVar[str] = "task.project-changed"
