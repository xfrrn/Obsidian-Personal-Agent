from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .value_objects import Identifier, VaultPath


class ResourceType(StrEnum):
    NOTE = "note"
    TASK = "task"
    PROJECT = "project"
    TAG = "tag"
    ATTACHMENT = "attachment"
    PLUGIN = "plugin"
    GRAPH = "graph"
    CONFIGURATION = "configuration"


@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceRef:
    resource_type: ResourceType
    resource_id: Identifier | None = None
    path: VaultPath | None = None
    title: str | None = None

    def __post_init__(self) -> None:
        if self.resource_id is None and self.path is None:
            raise ValueError("ResourceRef needs resource_id or path")
