"""LLM 层与核心层之间交换的纯数据类型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ClientError(RuntimeError):
    """模型端点无法完成请求或返回格式不合法。"""


class ContextLimitError(ClientError):
    """模型端点明确拒绝了超过其上下文窗口的请求。"""


@dataclass(frozen=True, slots=True)
class ToolCall:
    """模型要求调用一个函数工具。"""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """模型端点报告的一次请求精确 token 用量。"""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class AssistantResponse:
    """一次模型响应中的文本、工具调用和可选的回合终止信号。"""

    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: TokenUsage | None = None
    # 兼容端点通常不提供该字段；None 时由 tool_calls 决定是否继续。
    end_turn: bool | None = None
    reasoning: str | None = None
