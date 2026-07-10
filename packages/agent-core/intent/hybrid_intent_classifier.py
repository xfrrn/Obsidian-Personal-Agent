"""规则 + LLM 的混合意图识别器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from inspect import isawaitable
import json
from typing import Any, Callable

from prompts import build_intent_fallback_prompt

from .entity_extractor import EntityExtractor
from .intent_classifier import RuleBasedIntentClassifier
from .intent_types import (
    IntentCandidate,
    IntentEntity,
    IntentEntityType,
    IntentResult,
    IntentType,
)

LlmIntentClient = Callable[[str], Any]


@dataclass(frozen=True)
class HybridIntentClassifierOptions:
    """混合识别阈值配置。"""

    llm_fallback_threshold: float = 0.65
    llm_failure_fallback_to_rule: bool = True


@dataclass
class HybridIntentClassifier:
    """先规则识别，低置信度时再调用 LLM。"""

    llm_client: LlmIntentClient
    rule_classifier: RuleBasedIntentClassifier = field(default_factory=RuleBasedIntentClassifier)
    entity_extractor: EntityExtractor = field(default_factory=EntityExtractor)
    options: HybridIntentClassifierOptions = field(default_factory=HybridIntentClassifierOptions)

    async def classify(self, input_text: str) -> IntentResult:
        """识别意图；规则不确定时使用 LLM 兜底。"""
        rule_result = self.rule_classifier.classify(input_text)
        if rule_result.confidence >= self.options.llm_fallback_threshold:
            return rule_result

        try:
            return await self._classify_with_llm(input_text, rule_result)
        except Exception:
            if self.options.llm_failure_fallback_to_rule:
                return rule_result
            raise

    async def _classify_with_llm(self, input_text: str, rule_result: IntentResult) -> IntentResult:
        prompt = build_intent_fallback_prompt(
            input_text,
            [
                {
                    "intent": candidate.intent.value,
                    "score": candidate.score,
                    "matchedKeywords": list(candidate.matched_keywords),
                }
                for candidate in rule_result.candidates
            ],
        )
        response = self.llm_client(prompt)
        if isawaitable(response):
            response = await response
        payload = self._parse_json(str(response))
        return self._result_from_payload(input_text, payload, rule_result)

    def _parse_json(self, text: str) -> dict[str, Any]:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("LLM intent response is not JSON")
        value = json.loads(text[start : end + 1])
        if not isinstance(value, dict):
            raise ValueError("LLM intent response must be an object")
        return value

    def _result_from_payload(
        self,
        raw_text: str,
        payload: dict[str, Any],
        rule_result: IntentResult,
    ) -> IntentResult:
        intent = IntentType(str(payload.get("intent", IntentType.UNKNOWN.value)))
        confidence = self._confidence(payload.get("confidence"))
        entities = self._entities(payload.get("entities"))
        if not entities:
            entities = self.entity_extractor.extract(raw_text, intent)
        requires_confirmation = payload.get("requiresConfirmation")
        if not isinstance(requires_confirmation, bool):
            requires_confirmation = confidence < 0.65 or intent is IntentType.UNKNOWN

        llm_candidate = IntentCandidate(intent, confidence * 25, ("llm",))
        candidates = (llm_candidate, *rule_result.candidates[:2])
        return IntentResult(
            intent=intent,
            confidence=confidence,
            entities=entities,
            raw_text=raw_text,
            requires_confirmation=requires_confirmation,
            candidates=candidates,
        )

    def _confidence(self, value: Any) -> float:
        if isinstance(value, int | float):
            return max(0, min(float(value), 1))
        return 0

    def _entities(self, value: Any) -> tuple[IntentEntity, ...]:
        if not isinstance(value, list):
            return ()

        entities: list[IntentEntity] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            entity_type = item.get("type")
            entity_value = item.get("value")
            if not isinstance(entity_type, str) or not isinstance(entity_value, str):
                continue
            try:
                entities.append(IntentEntity(IntentEntityType(entity_type), entity_value))
            except ValueError:
                continue
        return tuple(entities)
