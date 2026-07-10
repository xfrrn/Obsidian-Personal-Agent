"""内存版对话状态管理器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Mapping
from uuid import uuid4

from exceptions import ConversationError


class ConversationRole(str, Enum):
    """对话消息角色。"""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


@dataclass(frozen=True)
class ConversationMessage:
    """一条对话消息。"""

    role: ConversationRole
    content: str
    conversation_id: str
    message_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: Mapping[str, object] = field(default_factory=dict)


class ConversationManager:
    """保存和读取最近对话历史。"""

    def __init__(self) -> None:
        self._messages: dict[str, list[ConversationMessage]] = {}

    def start_conversation(self, conversation_id: str | None = None) -> str:
        """创建对话并返回对话 ID。"""
        value = conversation_id or uuid4().hex
        self._messages.setdefault(value, [])
        return value

    def append(
        self,
        conversation_id: str,
        role: ConversationRole,
        content: str,
        metadata: Mapping[str, object] | None = None,
    ) -> ConversationMessage:
        """追加一条消息。"""
        if not content.strip():
            raise ConversationError("message content is required")
        self.start_conversation(conversation_id)
        message = ConversationMessage(
            role=role,
            content=content,
            conversation_id=conversation_id,
            metadata=metadata or {},
        )
        self._messages[conversation_id].append(message)
        return message

    def append_user_message(
        self,
        conversation_id: str,
        content: str,
        metadata: Mapping[str, object] | None = None,
    ) -> ConversationMessage:
        """追加用户消息。"""
        return self.append(conversation_id, ConversationRole.USER, content, metadata)

    def append_assistant_message(
        self,
        conversation_id: str,
        content: str,
        metadata: Mapping[str, object] | None = None,
    ) -> ConversationMessage:
        """追加助手消息。"""
        return self.append(conversation_id, ConversationRole.ASSISTANT, content, metadata)

    def messages(self, conversation_id: str) -> tuple[ConversationMessage, ...]:
        """返回某个对话的全部消息。"""
        return tuple(self._messages.get(conversation_id, ()))

    def recent_messages(
        self,
        conversation_id: str,
        limit: int = 12,
    ) -> tuple[ConversationMessage, ...]:
        """返回最近 N 条消息。"""
        if limit <= 0:
            return ()
        return tuple(self._messages.get(conversation_id, ())[-limit:])

    def clear(self, conversation_id: str) -> None:
        """清空某个对话。"""
        self._messages.pop(conversation_id, None)
