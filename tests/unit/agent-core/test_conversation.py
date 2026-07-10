from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "agent-core"))

from conversation import ContextBuilder, ConversationManager, ConversationRole, MemoryManager  # noqa: E402
from tools import ToolDefinition  # noqa: E402


def test_conversation_manager_keeps_recent_messages() -> None:
    manager = ConversationManager()
    conversation_id = manager.start_conversation("c1")
    manager.append_user_message(conversation_id, "问题 1")
    manager.append_assistant_message(conversation_id, "回答 1")
    manager.append_user_message(conversation_id, "问题 2")

    recent = manager.recent_messages(conversation_id, 2)
    assert [message.role for message in recent] == [ConversationRole.ASSISTANT, ConversationRole.USER]
    assert [message.content for message in recent] == ["回答 1", "问题 2"]


def test_context_builder_collects_turn_context() -> None:
    conversations = ConversationManager()
    memory = MemoryManager()
    conversations.append_user_message("c1", "之前的问题")
    memory.update_summary("c1", "用户正在开发 Agent Core")
    memory.set_user_preference("c1", "language", "zh-CN")

    context = ContextBuilder(conversations, memory, recent_message_limit=3).build(
        conversation_id="c1",
        user_input="下一步做什么",
        scope="vault",
        available_tools=(ToolDefinition("search_notes", "搜索笔记"),),
        active_file_path="README.md",
        selected_text="Agent",
    )

    assert context.conversation_id == "c1"
    assert context.recent_messages[0].content == "之前的问题"
    assert context.available_tools[0].name == "search_notes"
    assert context.memory.summary == "用户正在开发 Agent Core"
    assert context.memory.user_preferences["language"] == "zh-CN"
    assert context.active_file_path == "README.md"
    assert context.selected_text == "Agent"


if __name__ == "__main__":
    test_conversation_manager_keeps_recent_messages()
    test_context_builder_collects_turn_context()
