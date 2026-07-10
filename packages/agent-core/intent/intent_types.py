"""Agent 意图识别类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class IntentType(str, Enum):
    """Agent 支持识别的用户意图。"""

    NOTE_SEARCH = "note.search"
    NOTE_CREATE = "note.create"
    NOTE_UPDATE = "note.update"
    NOTE_SUMMARIZE = "note.summarize"
    NOTE_OPTIMIZE = "note.optimize"
    NOTE_TAG = "note.tag"
    NOTE_ARCHIVE = "note.archive"
    TASK_SEARCH = "task.search"
    TASK_CREATE = "task.create"
    TASK_UPDATE = "task.update"
    TASK_COMPLETE = "task.complete"
    TASK_DELETE = "task.delete"
    PROJECT_SEARCH = "project.search"
    PROJECT_ORGANIZE = "project.organize"
    VAULT_SEARCH = "vault.search"
    VAULT_ORGANIZE = "vault.organize"
    CHAT_GENERAL = "chat.general"
    UNKNOWN = "unknown"


class IntentEntityType(str, Enum):
    """从用户输入中抽取出的实体类别。"""

    NOTE_NAME = "note_name"
    PROJECT_NAME = "project_name"
    TASK_NAME = "task_name"
    TAG = "tag"
    DATE = "date"
    FOLDER = "folder"
    KEYWORD = "keyword"


@dataclass(frozen=True)
class IntentEntity:
    """用户输入中的一个实体。"""

    type: IntentEntityType
    value: str
    start: int | None = None
    end: int | None = None


@dataclass(frozen=True)
class IntentCandidate:
    """一个可能的意图候选。"""

    intent: IntentType
    score: float
    matched_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntentResult:
    """意图识别结果。"""

    intent: IntentType
    confidence: float
    entities: tuple[IntentEntity, ...]
    raw_text: str
    requires_confirmation: bool
    candidates: tuple[IntentCandidate, ...] = field(default_factory=tuple)
