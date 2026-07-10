"""轻量记忆管理器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from exceptions import ConversationError


@dataclass(frozen=True)
class MemorySnapshot:
    """构建上下文时使用的记忆快照。"""

    summary: str = ""
    user_preferences: Mapping[str, object] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)


class MemoryManager:
    """第一版内存记忆；后续可替换为数据库或摘要服务。"""

    def __init__(self) -> None:
        self._summaries: dict[str, str] = {}
        self._preferences: dict[str, dict[str, object]] = {}

    def snapshot(self, conversation_id: str) -> MemorySnapshot:
        """返回某个对话的记忆快照。"""
        return MemorySnapshot(
            summary=self._summaries.get(conversation_id, ""),
            user_preferences=dict(self._preferences.get(conversation_id, {})),
        )

    def update_summary(self, conversation_id: str, summary: str) -> None:
        """更新对话摘要。"""
        self._summaries[conversation_id] = summary.strip()

    def set_user_preference(self, conversation_id: str, key: str, value: object) -> None:
        """记录用户偏好。"""
        if not key.strip():
            raise ConversationError("preference key is required")
        self._preferences.setdefault(conversation_id, {})[key] = value

    def clear(self, conversation_id: str) -> None:
        """清空某个对话的记忆。"""
        self._summaries.pop(conversation_id, None)
        self._preferences.pop(conversation_id, None)
