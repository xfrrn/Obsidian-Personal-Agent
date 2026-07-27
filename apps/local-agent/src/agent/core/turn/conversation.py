"""模型可见对话历史及 tool-call/result 关联。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agent.llm.types import AssistantResponse
from agent.protocol.op import FileReference


INTERRUPTED_TOOL_CONTENT = "工具调用因回合中断而未完成。"


@dataclass(frozen=True, slots=True)
class InterruptedToolResult:
    call_id: str
    name: str
    content: str = INTERRUPTED_TOOL_CONTENT


class ConversationHistory:
    """维护模型历史；调用方无需了解 OpenAI tool message 的配对约束。"""

    def __init__(self) -> None:
        self._messages: list[dict[str, Any]] = []
        self._pending_tool_calls: dict[str, str] = {}

    @property
    def messages(self) -> list[dict[str, Any]]:
        """保留可变视图以兼容现有压缩测试；AgentLoop 应使用 snapshot()。"""

        return self._messages

    def snapshot(self) -> list[dict[str, Any]]:
        return [dict(message) for message in self._messages]

    def append_user(
        self, text: str, references: tuple[FileReference, ...] = ()
    ) -> dict[str, Any]:
        message = {"role": "user", "content": _render_user_content(text, references)}
        if references:
            message["display_content"] = text
            message["references"] = [{"path": reference.path} for reference in references]
        self._messages.append(message)
        return message

    def append_assistant(self, response: AssistantResponse) -> dict[str, Any]:
        message: dict[str, object] = {"role": "assistant", "content": response.content}
        if response.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
                }
                for call in response.tool_calls
            ]
            self._pending_tool_calls = {call.id: call.name for call in response.tool_calls}
        self._messages.append(message)
        return message

    def append_tool_result(self, call_id: str, name: str, content: str) -> dict[str, Any]:
        message = {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}
        self._messages.append(message)
        self._pending_tool_calls.pop(call_id, None)
        return message

    def complete_interrupted_tools(self) -> tuple[InterruptedToolResult, ...]:
        """先补齐全部历史，再让调用方发布中断事件，避免下一次请求携带半个 tool 交互。"""

        results = tuple(
            InterruptedToolResult(call_id, name)
            for call_id, name in self._pending_tool_calls.items()
        )
        for result in results:
            self.append_tool_result(result.call_id, result.name, result.content)
        return results

    def replace(self, messages: list[dict[str, Any]]) -> None:
        self._messages[:] = (dict(message) for message in messages)
        self._pending_tool_calls.clear()
        # 恢复可能发生在工具执行中途；重建未完成调用后，调用方才能补齐中断结果。
        for message in self._messages:
            if message.get("role") == "assistant":
                for call in message.get("tool_calls") or ():
                    try:
                        self._pending_tool_calls[call["id"]] = call["function"]["name"]
                    except (KeyError, TypeError) as exc:
                        raise ValueError("对话历史包含格式无效的工具调用") from exc
            elif message.get("role") == "tool":
                call_id = message.get("tool_call_id")
                if isinstance(call_id, str):
                    self._pending_tool_calls.pop(call_id, None)


def _render_user_content(text: str, references: tuple[FileReference, ...]) -> str:
    if not references:
        return text
    paths = "\n".join(f"@{reference.path}" for reference in references)
    return f"{text.strip()}\n\n{paths}" if text.strip() else paths
