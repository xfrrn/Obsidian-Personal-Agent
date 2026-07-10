from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from oka_domain.common import ProjectId, TagName, VaultPath
from .models import Project, ProjectStatus


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectQuery:
    statuses: tuple[ProjectStatus, ...] = ()
    parent_project_ids: tuple[ProjectId, ...] = ()
    tags: tuple[TagName, ...] = ()
    root_paths: tuple[VaultPath, ...] = ()
    limit: int = 100


class ProjectRepository(Protocol):
    async def get(self, project_id: ProjectId) -> Project | None: ...

    async def get_by_root_path(self, root_path: VaultPath) -> Project | None: ...

    async def list(self, query: ProjectQuery) -> Sequence[Project]: ...

    async def save(self, project: Project) -> None: ...

    async def delete(self, project_id: ProjectId) -> None: ...
