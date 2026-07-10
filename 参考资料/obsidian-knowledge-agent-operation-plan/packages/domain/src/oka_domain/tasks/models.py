from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from oka_domain.common import (
    AggregateRoot,
    NoteId,
    ProjectId,
    TagName,
    TaskId,
    VaultPath,
    utc_now,
)
from oka_domain.exceptions import InvalidStateTransition, ValidationError
from .events import TaskCreated, TaskProjectChanged, TaskRescheduled, TaskStatusChanged


class TaskStatus(StrEnum):
    TODO = "todo"
    DOING = "doing"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


@dataclass(slots=True, kw_only=True)
class Task(AggregateRoot):
    task_id: TaskId
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    project_id: ProjectId | None = None
    source_note_id: NoteId | None = None
    source_note_path: VaultPath | None = None
    target_path: VaultPath | None = None
    parent_task_id: TaskId | None = None
    dependency_ids: set[TaskId] = field(default_factory=set)
    tags: set[TagName] = field(default_factory=set)
    due_at: datetime | None = None
    scheduled_at: datetime | None = None
    completed_at: datetime | None = None
    blocked_reason: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.title = self.title.strip()
        if not self.title:
            raise ValidationError("Task title cannot be blank")
        if self.parent_task_id == self.task_id:
            raise ValidationError("Task cannot be its own parent")
        if self.task_id in self.dependency_ids:
            raise ValidationError("Task cannot depend on itself")
        if self.status is TaskStatus.DONE and self.completed_at is None:
            self.completed_at = self.updated_at
        if self.status is TaskStatus.BLOCKED and not self.blocked_reason:
            raise ValidationError("Blocked task must include blocked_reason")

    @classmethod
    def create(
        cls,
        *,
        title: str,
        description: str = "",
        priority: TaskPriority = TaskPriority.MEDIUM,
        project_id: ProjectId | None = None,
        due_at: datetime | None = None,
        target_path: VaultPath | None = None,
        source_note_id: NoteId | None = None,
        source_note_path: VaultPath | None = None,
        tags: set[TagName] | None = None,
    ) -> Task:
        task = cls(
            task_id=TaskId.new(),
            title=title,
            description=description,
            priority=priority,
            project_id=project_id,
            due_at=due_at,
            target_path=target_path,
            source_note_id=source_note_id,
            source_note_path=source_note_path,
            tags=tags or set(),
        )
        task._record(
            TaskCreated(
                aggregate_id=str(task.task_id),
                payload={"title": task.title, "priority": task.priority.value},
            )
        )
        return task

    def start(self) -> None:
        self._transition(TaskStatus.DOING)

    def block(self, reason: str) -> None:
        reason = reason.strip()
        if not reason:
            raise ValidationError("Blocked reason cannot be blank")
        self.blocked_reason = reason
        self._transition(TaskStatus.BLOCKED)

    def complete(self) -> None:
        if self.status is TaskStatus.CANCELLED:
            raise InvalidStateTransition("Cancelled task cannot be completed")
        self.completed_at = utc_now()
        self.blocked_reason = None
        self._transition(TaskStatus.DONE)

    def cancel(self) -> None:
        if self.status is TaskStatus.DONE:
            raise InvalidStateTransition("Completed task cannot be cancelled")
        self.completed_at = None
        self.blocked_reason = None
        self._transition(TaskStatus.CANCELLED)

    def reopen(self) -> None:
        if self.status not in {TaskStatus.DONE, TaskStatus.CANCELLED, TaskStatus.BLOCKED}:
            raise InvalidStateTransition("Only done, cancelled or blocked tasks can be reopened")
        self.completed_at = None
        self.blocked_reason = None
        self._transition(TaskStatus.TODO)

    def reschedule(self, due_at: datetime | None) -> None:
        old_due_at = self.due_at
        if old_due_at == due_at:
            return
        self.due_at = due_at
        self._touch()
        self._changed(
            TaskRescheduled(
                aggregate_id=str(self.task_id),
                payload={
                    "oldDueAt": old_due_at.isoformat() if old_due_at else None,
                    "newDueAt": due_at.isoformat() if due_at else None,
                },
            )
        )

    def assign_project(self, project_id: ProjectId | None) -> None:
        if project_id == self.project_id:
            return
        old_project = self.project_id
        self.project_id = project_id
        self._touch()
        self._changed(
            TaskProjectChanged(
                aggregate_id=str(self.task_id),
                payload={
                    "oldProjectId": str(old_project) if old_project else None,
                    "newProjectId": str(project_id) if project_id else None,
                },
            )
        )

    def add_dependency(self, task_id: TaskId) -> None:
        if task_id == self.task_id:
            raise ValidationError("Task cannot depend on itself")
        if task_id not in self.dependency_ids:
            self.dependency_ids.add(task_id)
            self._touch()
            self.revision += 1

    def remove_dependency(self, task_id: TaskId) -> None:
        if task_id in self.dependency_ids:
            self.dependency_ids.remove(task_id)
            self._touch()
            self.revision += 1

    def _transition(self, new_status: TaskStatus) -> None:
        if new_status == self.status:
            return
        allowed: dict[TaskStatus, set[TaskStatus]] = {
            TaskStatus.TODO: {TaskStatus.DOING, TaskStatus.BLOCKED, TaskStatus.DONE, TaskStatus.CANCELLED},
            TaskStatus.DOING: {TaskStatus.TODO, TaskStatus.BLOCKED, TaskStatus.DONE, TaskStatus.CANCELLED},
            TaskStatus.BLOCKED: {TaskStatus.TODO, TaskStatus.DOING, TaskStatus.DONE, TaskStatus.CANCELLED},
            TaskStatus.DONE: {TaskStatus.TODO},
            TaskStatus.CANCELLED: {TaskStatus.TODO},
        }
        if new_status not in allowed[self.status]:
            raise InvalidStateTransition(f"Cannot move task from {self.status} to {new_status}")
        old_status = self.status
        self.status = new_status
        self._touch()
        self._changed(
            TaskStatusChanged(
                aggregate_id=str(self.task_id),
                payload={"oldStatus": old_status.value, "newStatus": new_status.value},
            )
        )

    def _touch(self) -> None:
        self.updated_at = utc_now()
