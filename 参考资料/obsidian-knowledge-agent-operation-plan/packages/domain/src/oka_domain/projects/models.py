from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from oka_domain.common import AggregateRoot, ProjectId, TagName, VaultPath, utc_now
from oka_domain.exceptions import InvalidStateTransition, ValidationError
from .events import (
    ProjectCreated,
    ProjectDocumentRegistered,
    ProjectMilestoneChanged,
    ProjectStatusChanged,
)


class ProjectStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    ON_HOLD = "on-hold"
    COMPLETED = "completed"
    ARCHIVED = "archived"
    CANCELLED = "cancelled"


class ProjectDocumentRole(StrEnum):
    OVERVIEW = "overview"
    REQUIREMENTS = "requirements"
    DESIGN = "design"
    TASKS = "tasks"
    DECISIONS = "decisions"
    ISSUES = "issues"
    CHANGELOG = "changelog"
    README = "readme"


class MilestoneStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass(slots=True, kw_only=True)
class ProjectMilestone:
    milestone_id: str
    title: str
    status: MilestoneStatus = MilestoneStatus.PLANNED
    due_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        self.title = self.title.strip()
        if not self.milestone_id.strip() or not self.title:
            raise ValidationError("Milestone id and title cannot be blank")


@dataclass(slots=True, kw_only=True)
class Project(AggregateRoot):
    project_id: ProjectId
    name: str
    root_path: VaultPath
    description: str = ""
    status: ProjectStatus = ProjectStatus.PLANNED
    parent_project_id: ProjectId | None = None
    tags: set[TagName] = field(default_factory=set)
    documents: dict[str, VaultPath] = field(default_factory=dict)
    milestones: dict[str, ProjectMilestone] = field(default_factory=dict)
    start_at: datetime | None = None
    target_end_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        if not self.name:
            raise ValidationError("Project name cannot be blank")
        if self.parent_project_id == self.project_id:
            raise ValidationError("Project cannot be its own parent")

    @classmethod
    def create(
        cls,
        *,
        name: str,
        root_path: VaultPath,
        description: str = "",
        parent_project_id: ProjectId | None = None,
        tags: set[TagName] | None = None,
    ) -> Project:
        project = cls(
            project_id=ProjectId.new(),
            name=name,
            root_path=root_path,
            description=description,
            parent_project_id=parent_project_id,
            tags=tags or set(),
        )
        project._record(
            ProjectCreated(
                aggregate_id=str(project.project_id),
                payload={"name": project.name, "rootPath": str(project.root_path)},
            )
        )
        return project

    def activate(self) -> None:
        if self.status not in {ProjectStatus.PLANNED, ProjectStatus.ON_HOLD}:
            raise InvalidStateTransition(f"Cannot activate project from {self.status}")
        if self.start_at is None:
            self.start_at = utc_now()
        self._transition(ProjectStatus.ACTIVE)

    def hold(self) -> None:
        if self.status is not ProjectStatus.ACTIVE:
            raise InvalidStateTransition("Only active projects can be put on hold")
        self._transition(ProjectStatus.ON_HOLD)

    def complete(self) -> None:
        if self.status not in {ProjectStatus.ACTIVE, ProjectStatus.ON_HOLD}:
            raise InvalidStateTransition("Only active or on-hold projects can be completed")
        self.completed_at = utc_now()
        self._transition(ProjectStatus.COMPLETED)

    def archive(self) -> None:
        if self.status is not ProjectStatus.COMPLETED:
            raise InvalidStateTransition("Only completed projects can be archived")
        self._transition(ProjectStatus.ARCHIVED)

    def cancel(self) -> None:
        if self.status in {ProjectStatus.COMPLETED, ProjectStatus.ARCHIVED}:
            raise InvalidStateTransition("Completed or archived project cannot be cancelled")
        self._transition(ProjectStatus.CANCELLED)

    def register_document(self, role: ProjectDocumentRole | str, path: VaultPath) -> None:
        role_value = role.value if isinstance(role, ProjectDocumentRole) else role.strip()
        if not role_value:
            raise ValidationError("Document role cannot be blank")
        if not path.is_markdown:
            raise ValidationError("Project document must be a Markdown file")
        self.documents[role_value] = path
        self._touch()
        self._changed(
            ProjectDocumentRegistered(
                aggregate_id=str(self.project_id),
                payload={"role": role_value, "path": str(path)},
            )
        )

    def add_milestone(self, milestone: ProjectMilestone) -> None:
        if milestone.milestone_id in self.milestones:
            raise ValidationError(f"Duplicate milestone id: {milestone.milestone_id}")
        self.milestones[milestone.milestone_id] = milestone
        self._touch()
        self._changed(
            ProjectMilestoneChanged(
                aggregate_id=str(self.project_id),
                payload={"action": "added", "milestoneId": milestone.milestone_id},
            )
        )

    def complete_milestone(self, milestone_id: str) -> None:
        milestone = self.milestones.get(milestone_id)
        if milestone is None:
            raise ValidationError(f"Unknown milestone id: {milestone_id}")
        milestone.status = MilestoneStatus.COMPLETED
        milestone.completed_at = utc_now()
        self._touch()
        self._changed(
            ProjectMilestoneChanged(
                aggregate_id=str(self.project_id),
                payload={"action": "completed", "milestoneId": milestone_id},
            )
        )

    def missing_standard_documents(self) -> set[ProjectDocumentRole]:
        existing = set(self.documents)
        return {role for role in ProjectDocumentRole if role.value not in existing}

    def _transition(self, new_status: ProjectStatus) -> None:
        old_status = self.status
        if old_status == new_status:
            return
        self.status = new_status
        self._touch()
        self._changed(
            ProjectStatusChanged(
                aggregate_id=str(self.project_id),
                payload={"oldStatus": old_status.value, "newStatus": new_status.value},
            )
        )

    def _touch(self) -> None:
        self.updated_at = utc_now()
