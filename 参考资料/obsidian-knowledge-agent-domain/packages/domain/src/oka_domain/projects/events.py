from dataclasses import dataclass
from typing import ClassVar

from oka_domain.common.events import DomainEvent


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectCreated(DomainEvent):
    event_type: ClassVar[str] = "project.created"


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectStatusChanged(DomainEvent):
    event_type: ClassVar[str] = "project.status-changed"


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectDocumentRegistered(DomainEvent):
    event_type: ClassVar[str] = "project.document-registered"


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectMilestoneChanged(DomainEvent):
    event_type: ClassVar[str] = "project.milestone-changed"
