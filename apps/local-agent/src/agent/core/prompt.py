"""把 Session 历史组装为 OpenAI 兼容的聊天消息。"""

from __future__ import annotations

from typing import Any


def build_messages(
    system_prompt: str,
    history: list[dict[str, Any]],
    context_messages: tuple[str, ...] = (),
    compacted_summary: str | None = None,
) -> list[dict[str, Any]]:
    """返回本回合的提示快照。

    每次请求都重新生成列表，避免 HTTP 客户端或测试替身意外修改 Session 历史；
    系统消息始终位于首位，防止工具输出被误当成高优先级指令。
    """

    return [
        {"role": "system", "content": system_prompt},
        *({"role": "system", "content": message} for message in context_messages),
        *([{"role": "assistant", "content": compacted_summary}] if compacted_summary else []),
        *(dict(message) for message in history),
    ]


def split_history_for_compaction(
    history: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """返回可摘要的旧块和必须原样保留的最新块。"""

    blocks = _history_blocks(history)
    if len(blocks) < 2:
        return [], [dict(message) for block in blocks for message in block]
    return (
        [dict(message) for block in blocks[:-1] for message in block],
        [dict(message) for message in blocks[-1]],
    )


def _history_blocks(history: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """按 user 消息划分交互块，避免拆开 tool call 与对应结果。"""

    blocks: list[list[dict[str, Any]]] = []
    current_block: list[dict[str, Any]] = []
    for message in history:
        if message.get("role") == "user" and current_block:
            blocks.append(current_block)
            current_block = []
        current_block.append(message)
    if current_block:
        blocks.append(current_block)
    return [block for block in blocks if _has_complete_tool_results(block)]


def _has_complete_tool_results(block: list[dict[str, Any]]) -> bool:
    """丢弃 steering 取消后遗留的半个工具交互，避免违反 API 消息关联约束。"""

    pending_call_ids: set[str] = set()
    for message in block:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or ():
                if not isinstance(call, dict) or not isinstance(call_id := call.get("id"), str):
                    return False
                pending_call_ids.add(call_id)
        elif message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in pending_call_ids:
                return False
            pending_call_ids.remove(call_id)
    return not pending_call_ids
