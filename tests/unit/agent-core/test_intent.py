from __future__ import annotations

from pathlib import Path
import sys
import asyncio

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "agent-core"))

from intent import HybridIntentClassifier, HybridIntentClassifierOptions, RuleBasedIntentClassifier  # noqa: E402
from intent.intent_types import IntentEntityType, IntentType  # noqa: E402


def _entity_values(result, entity_type):
    return [entity.value for entity in result.entities if entity.type is entity_type]


def test_task_search_intent() -> None:
    result = RuleBasedIntentClassifier().classify("查询一下本周还有哪些任务")
    assert result.intent is IntentType.TASK_SEARCH
    assert result.confidence >= 0.65
    assert "本周" in _entity_values(result, IntentEntityType.DATE)


def test_task_create_intent() -> None:
    result = RuleBasedIntentClassifier().classify("提醒我明天完成 Obsidian 插件设置页面")
    assert result.intent is IntentType.TASK_CREATE
    assert "明天" in _entity_values(result, IntentEntityType.DATE)
    assert _entity_values(result, IntentEntityType.TASK_NAME)


def test_note_search_intent() -> None:
    result = RuleBasedIntentClassifier().classify("搜索知识库里关于 RAG 切片的笔记")
    assert result.intent in {IntentType.NOTE_SEARCH, IntentType.VAULT_SEARCH}
    assert result.candidates


def test_unknown_intent() -> None:
    result = RuleBasedIntentClassifier().classify("嗯")
    assert result.intent is IntentType.UNKNOWN
    assert result.requires_confirmation


def test_greeting_is_general_chat() -> None:
    result = RuleBasedIntentClassifier().classify("你好")
    assert result.intent is IntentType.CHAT_GENERAL
    assert not result.requires_confirmation


def test_p1_p2_read_intents() -> None:
    cases = {
        "检查当前笔记是否符合规范": IntentType.NOTE_INSPECT,
        "分析一下 Agent 项目": IntentType.PROJECT_ANALYZE,
        "给知识库做一次健康检查": IntentType.VAULT_HEALTH,
        "列出标签统计": IntentType.TAG_LIST,
        "查找关联笔记": IntentType.NOTE_RELATED,
        "检查重复笔记": IntentType.NOTE_DUPLICATES,
        "列出知识库规则": IntentType.RULE_LIST,
        "试运行规则": IntentType.RULE_EVALUATE,
        "从当前笔记提取潜在任务": IntentType.TASK_EXTRACT,
    }
    classifier = RuleBasedIntentClassifier()
    for text, expected in cases.items():
        result = classifier.classify(text)
        assert result.intent is expected, (text, result.intent, result.candidates)
        assert not result.requires_confirmation

    project = classifier.classify("分析一下 Agent 项目")
    assert "Agent" in _entity_values(project, IntentEntityType.PROJECT_NAME)


def test_hybrid_uses_llm_when_rule_confidence_is_low() -> None:
    async def fake_llm(_prompt):
        return '{"intent":"note.search","confidence":0.91,"requiresConfirmation":false,"entities":[{"type":"keyword","value":"项目结构设计"}]}'

    result = asyncio.run(HybridIntentClassifier(fake_llm).classify("我记得之前写过项目结构设计，找一下"))
    assert result.intent is IntentType.NOTE_SEARCH
    assert result.confidence == 0.91
    assert _entity_values(result, IntentEntityType.KEYWORD) == ["项目结构设计"]


def test_hybrid_keeps_rule_result_when_confidence_is_high() -> None:
    called = False

    async def fake_llm(_prompt):
        nonlocal called
        called = True
        return "{}"

    result = asyncio.run(HybridIntentClassifier(fake_llm).classify("查询一下本周还有哪些任务"))
    assert result.intent is IntentType.TASK_SEARCH
    assert not called


def test_hybrid_rejects_text_wrapped_json() -> None:
    async def fake_llm(_prompt):
        return '说明：{"intent":"note.search","confidence":0.9}'

    classifier = HybridIntentClassifier(
        fake_llm,
        options=HybridIntentClassifierOptions(llm_failure_fallback_to_rule=False),
    )
    try:
        asyncio.run(classifier.classify("嗯"))
    except ValueError:
        pass
    else:
        raise AssertionError("wrapped JSON should fail")


if __name__ == "__main__":
    test_task_search_intent()
    test_task_create_intent()
    test_note_search_intent()
    test_unknown_intent()
    test_greeting_is_general_chat()
    test_hybrid_uses_llm_when_rule_confidence_is_low()
    test_hybrid_keeps_rule_result_when_confidence_is_high()
    test_hybrid_rejects_text_wrapped_json()
