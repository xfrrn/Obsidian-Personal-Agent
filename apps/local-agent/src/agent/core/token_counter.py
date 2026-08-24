"""请求前估算与模型响应 usage 校准。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from agent.llm.types import TokenUsage

try:
    import tiktoken
except ImportError:  # pragma: no cover - 部署环境可只依赖服务端 usage。
    tiktoken = None


_IMAGE_TOKEN_ALLOWANCE = 8_192


class ContextBudgetError(ValueError):
    """自动压缩后仍无法容纳当前请求。"""


@dataclass(slots=True)
class TokenCounter:
    """统一维护估算值和服务端确认的实际 token 用量。"""

    model: str
    last_estimated_prompt_tokens: int = 0
    last_usage: TokenUsage | None = None
    reported_request_count: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    _calibration_factor: float = 1.0
    _encoding: Any = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        if tiktoken is None:
            return
        try:
            self._encoding = tiktoken.encoding_for_model(self.model)
        except KeyError:
            # 兼容端点的模型名可能未知；此时由服务端 usage 校准通用估算。
            self._encoding = None

    def estimate_request(self, messages: list[dict[str, Any]], tool_specs: list[dict[str, Any]]) -> int:
        """返回用于压缩触发和超限保护的校准后输入估算。"""

        base_tokens = self._base_estimate(messages, tool_specs)
        return max(1, math.ceil(base_tokens * self._calibration_factor))

    def tokens_remaining(self, limit: int) -> int:
        """根据最近一次请求返回距离指定上下文边界的保守剩余量。"""

        reported_tokens = self.last_usage.prompt_tokens if self.last_usage else 0
        active_tokens = max(self.last_estimated_prompt_tokens, reported_tokens)
        return max(0, limit - active_tokens)

    def record_response(
        self,
        messages: list[dict[str, Any]],
        tool_specs: list[dict[str, Any]],
        usage: TokenUsage | None,
    ) -> None:
        """记录一次请求；端点提供 usage 时以它为准并只向上校准估算。"""

        base_tokens = self._base_estimate(messages, tool_specs)
        self.last_estimated_prompt_tokens = max(1, math.ceil(base_tokens * self._calibration_factor))
        self.last_usage = usage
        if usage is None:
            return

        self.reported_request_count += 1
        self.total_prompt_tokens += usage.prompt_tokens
        self.total_completion_tokens += usage.completion_tokens
        self.total_tokens += usage.total_tokens
        # 不向下校准，保证未知消息开销或兼容端点差异不会放松预算保护。
        self._calibration_factor = max(self._calibration_factor, usage.prompt_tokens / max(base_tokens, 1))

    def _base_estimate(self, messages: list[dict[str, Any]], tool_specs: list[dict[str, Any]]) -> int:
        return (
            3
            + sum(self._json_tokens(message) + 4 for message in messages)
            + sum(self._json_tokens(tool) + 12 for tool in tool_specs)
            + _IMAGE_TOKEN_ALLOWANCE * sum(_image_input_count(message) for message in messages)
        )

    def _json_tokens(self, value: object) -> int:
        payload = json.dumps(
            _redact_image_data(value), ensure_ascii=False, separators=(",", ":")
        )
        if self._encoding is not None:
            return len(self._encoding.encode(payload))

        ascii_characters = sum(character.isascii() for character in payload)
        non_ascii_characters = len(payload) - ascii_characters
        return math.ceil(ascii_characters / 3) + non_ascii_characters


def _redact_image_data(value: object) -> object:
    if isinstance(value, dict):
        if value.get("type") == "image_url" and isinstance(
            image_url := value.get("image_url"), dict
        ):
            url = image_url.get("url")
            if isinstance(url, str) and url.startswith("data:image/"):
                return {
                    **value,
                    "image_url": {**image_url, "url": "data:image/[base64 omitted]"},
                }
        return {key: _redact_image_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_image_data(item) for item in value]
    return value


def _image_input_count(value: object) -> int:
    if isinstance(value, dict):
        return (value.get("type") == "image_url") + sum(
            _image_input_count(item) for item in value.values()
        )
    if isinstance(value, (list, tuple)):
        return sum(_image_input_count(item) for item in value)
    return 0
