from dataclasses import dataclass
from typing import ClassVar

from oka_domain.common.events import DomainEvent


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationPlanCreated(DomainEvent):
    event_type: ClassVar[str] = "operation-plan.created"


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationPlanConfirmed(DomainEvent):
    event_type: ClassVar[str] = "operation-plan.confirmed"


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationPlanStatusChanged(DomainEvent):
    event_type: ClassVar[str] = "operation-plan.status-changed"
