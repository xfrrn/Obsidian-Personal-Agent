"""查询模型可用上下文与自动窗口切换余量。"""

from __future__ import annotations

import json

from agent.config.loader import build_mode_system_prompt
from agent.core.token_counter import TokenCounter
from agent.permissions import ToolAccess
from agent.protocol.mode import ModeKind
from agent.tools.types import ToolSpec


class GetContextRemainingTool:
    """查询最近一次模型请求的可用上下文与阈值余量。"""

    supports_parallel_tool_calls = True
    required_access = ToolAccess.READ_ONLY
    spec = ToolSpec(
        name="get_context_remaining",
        description=(
            "返回当前上下文可用输入的估算剩余 token，以及距离自动压缩阈值的剩余 token。"
            "mode=body_after_prefix 时额外预留系统提示词所占空间。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["total", "body_after_prefix"],
                    "description": "默认 total；body_after_prefix 返回更保守的正文余量。",
                }
            },
            "additionalProperties": False,
        },
    )

    def __init__(
        self,
        token_counter: TokenCounter,
        auto_compact_threshold: int,
        input_token_budget: int,
        system_prompt: str = "",
    ) -> None:
        self._token_counter = token_counter
        self._auto_compact_threshold = auto_compact_threshold
        self._input_token_budget = input_token_budget
        self._system_prompt = system_prompt

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> str:
        if set(arguments) - {"mode"}:
            raise ValueError("该工具只接受 mode 参数")
        remaining_mode = arguments.get("mode", "total")
        if remaining_mode not in {"total", "body_after_prefix"}:
            raise ValueError("mode 必须是 total 或 body_after_prefix")
        prefix_tokens = (
            self._token_counter.estimate_request(
                [
                    {
                        "role": "system",
                        "content": build_mode_system_prompt(self._system_prompt, mode),
                    }
                ],
                [],
            )
            if remaining_mode == "body_after_prefix"
            else 0
        )
        return json.dumps(
            {
                "tokens_left": max(
                    0,
                    self._token_counter.tokens_remaining(self._input_token_budget)
                    - prefix_tokens,
                ),
                "tokens_until_compaction": max(
                    0,
                    self._token_counter.tokens_remaining(self._auto_compact_threshold)
                    - prefix_tokens,
                ),
            },
            ensure_ascii=False,
        )
