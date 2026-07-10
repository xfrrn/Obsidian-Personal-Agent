from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence

from oka_domain.common import ProjectId, TagName, TaskId
from .models import Task, TaskPriority, TaskStatus


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskQuery:
    project_ids: tuple[ProjectId, ...] = ()
    statuses: tuple[TaskStatus, ...] = ()
    priorities: tuple[TaskPriority, ...] = ()
    tags: tuple[TagName, ...] = ()
    due_before: datetime | None = None
    due_after: datetime | None = None
    limit: int = 100


class TaskRepository(Protocol):
    async def get(self, task_id: TaskId) -> Task | None: ...

    async def list(self, query: TaskQuery) -> Sequence[Task]: ...

    async def save(self, task: Task) -> None: ...

    async def delete(self, task_id: TaskId) -> None: ...
