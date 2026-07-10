"""构建 Agent 单轮运行上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from exceptions import ConversationError
from intent.intent_types import IntentResult
from tools.definitions import ToolDefinition

from .conversation_manager import ConversationManager, ConversationMessage
from .memory_manager import MemoryManager, MemorySnapshot


@dataclass(frozen=True)
class AgentTurnContext:
    """一次 Agent 回合需要的全部上下文。"""

    conversation_id: str
    user_input: str
    scope: str
    intent: IntentResult | None
    recent_messages: tuple[ConversationMessage, ...]
    available_tools: tuple[ToolDefinition, ...]
    memory: MemorySnapshot = field(default_factory=MemorySnapshot)
    active_file_path: str | None = None
    selected_text: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


class ContextBuilder:
    """从对话历史、记忆和运行参数组装上下文。"""

    def __init__(
        self,
        conversations: ConversationManager,
        memory: MemoryManager | None = None,
        recent_message_limit: int = 12,
    ) -> None:
        if recent_message_limit < 0:
            raise ConversationError("recent_message_limit cannot be negative")
        self._conversations = conversations
        self._memory = memory or MemoryManager()
        self._recent_message_limit = recent_message_limit

    def build(
        self,
        *,
        conversation_id: str,
        user_input: str,
        scope: str = "vault",
        intent: IntentResult | None = None,
        available_tools: tuple[ToolDefinition, ...] = (),
        active_file_path: str | None = None,
        selected_text: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> AgentTurnContext:
        """构建当前回合上下文。"""
        if not user_input.strip():
            raise ConversationError("user_input is required")

        self._conversations.start_conversation(conversation_id)
        return AgentTurnContext(
            conversation_id=conversation_id,
            user_input=user_input,
            scope=scope,
            intent=intent,
            recent_messages=self._conversations.recent_messages(
                conversation_id,
                self._recent_message_limit,
            ),
            available_tools=available_tools,
            memory=self._memory.snapshot(conversation_id),
            active_file_path=active_file_path,
            selected_text=selected_text,
            metadata=metadata or {},
        )
