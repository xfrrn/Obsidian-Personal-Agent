"""意图识别相关提示词。"""

from __future__ import annotations

import json
from typing import Iterable, Mapping

from intent.intent_types import IntentType


def build_intent_fallback_prompt(
    input_text: str,
    candidates: Iterable[Mapping[str, object]],
) -> str:
    """构造低置信度时交给 LLM 的意图识别提示词。"""
    intents = ", ".join(intent.value for intent in IntentType)
    return (
        "你是 Obsidian Personal Agent 的意图识别器。只返回 JSON，不要输出其他文字。\n"
        f"可选 intent：{intents}\n"
        "JSON 格式："
        '{"intent":"task.search","confidence":0.8,'
        '"requiresConfirmation":false,'
        '"entities":[{"type":"keyword","value":"示例"}]}\n'
        f"规则识别候选：{json.dumps(list(candidates), ensure_ascii=False)}\n"
        f"用户输入：{input_text}"
    )
