"""基于规则和关键词评分的意图识别器。"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from .entity_extractor import EntityExtractor
from .intent_types import IntentCandidate, IntentResult, IntentType


@dataclass(frozen=True)
class IntentRule:
    """单个意图的匹配规则。"""

    intent: IntentType
    strong_keywords: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    negative_keywords: tuple[str, ...] = ()
    patterns: tuple[re.Pattern[str], ...] = ()
    priority: int = 0


@dataclass(frozen=True)
class IntentClassifierOptions:
    """意图识别阈值配置。"""

    unknown_threshold: float = 0.35
    confirmation_threshold: float = 0.65


def _patterns(*values: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(value) for value in values)


INTENT_RULES: tuple[IntentRule, ...] = (
    IntentRule(
        IntentType.TASK_COMPLETE,
        strong_keywords=("完成任务", "标记完成", "设为完成", "任务已完成"),
        keywords=("完成", "做完", "结束任务", "打勾"),
        patterns=_patterns(r"把.+任务.*完成", r"将.+标记为完成", r"完成一下.+"),
        priority=10,
    ),
    IntentRule(
        IntentType.TASK_CREATE,
        strong_keywords=("创建任务", "新建任务", "添加任务", "记录任务"),
        keywords=("提醒我", "待办", "todo", "需要完成", "安排一个任务", "加到任务"),
        patterns=_patterns(r"提醒我.+", r"创建一个.+任务", r"新建一个.+待办", r"(?:今天|明天|后天|下周).*(?:要|需要|记得).+"),
        priority=9,
    ),
    IntentRule(
        IntentType.TASK_UPDATE,
        strong_keywords=("修改任务", "更新任务", "编辑任务"),
        keywords=("延期", "改成", "调整截止日期", "修改优先级"),
        patterns=_patterns(r"把.+任务.*改成", r"将.+截止日期.*改为", r"把.+延期到"),
        priority=8,
    ),
    IntentRule(
        IntentType.TASK_DELETE,
        strong_keywords=("删除任务", "移除任务", "取消任务"),
        keywords=("删掉待办", "不要这个任务"),
        patterns=_patterns(r"删除.+任务", r"取消.+待办"),
        priority=9,
    ),
    IntentRule(
        IntentType.TASK_SEARCH,
        strong_keywords=("查询任务", "查找任务", "任务列表"),
        keywords=("有哪些任务", "本周任务", "今天的任务", "未完成任务", "待办事项"),
        patterns=_patterns(r"我有(?:哪些|什么)任务", r"(?:今天|本周|这个月).*(?:任务|待办)", r"查看.+任务"),
        priority=7,
    ),
    IntentRule(
        IntentType.NOTE_CREATE,
        strong_keywords=("创建笔记", "新建笔记", "添加笔记"),
        keywords=("写一篇笔记", "记录一下", "保存为笔记"),
        patterns=_patterns(r"创建一篇.+笔记", r"新建一个.+文档", r"把.+记录下来"),
        priority=8,
    ),
    IntentRule(
        IntentType.NOTE_UPDATE,
        strong_keywords=("修改笔记", "更新笔记", "编辑笔记"),
        keywords=("补充内容", "追加内容", "改一下笔记"),
        patterns=_patterns(r"给.+笔记.*补充", r"在.+笔记.*加入", r"修改.+笔记"),
        priority=8,
    ),
    IntentRule(
        IntentType.NOTE_OPTIMIZE,
        strong_keywords=("优化笔记", "润色笔记", "整理语句", "优化表达"),
        keywords=("润色", "改写", "让语句更通顺", "优化一下"),
        patterns=_patterns(r"优化.+(?:笔记|内容|文章)", r"润色.+", r"把.+写得更"),
        priority=8,
    ),
    IntentRule(
        IntentType.NOTE_SUMMARIZE,
        strong_keywords=("总结笔记", "生成摘要", "概括笔记"),
        keywords=("总结", "摘要", "概括", "提炼重点"),
        patterns=_patterns(r"总结一下.+", r"给.+生成摘要", r"提炼.+重点"),
        priority=8,
    ),
    IntentRule(
        IntentType.NOTE_TAG,
        strong_keywords=("添加标签", "生成标签", "修改标签"),
        keywords=("打标签", "加标签", "标签分类"),
        patterns=_patterns(r"给.+添加.+标签", r"给.+打标签", r"生成适合的标签"),
        priority=8,
    ),
    IntentRule(
        IntentType.NOTE_ARCHIVE,
        strong_keywords=("归档笔记", "移动笔记", "整理到目录"),
        keywords=("归档", "移动到", "放到文件夹"),
        patterns=_patterns(r"把.+移动到.+", r"把.+归档到.+", r"将.+放入.+目录"),
        priority=8,
    ),
    IntentRule(
        IntentType.NOTE_SEARCH,
        strong_keywords=("查询笔记", "搜索笔记", "查找笔记"),
        keywords=("找一下", "有没有记录", "之前写过", "哪篇笔记", "相关笔记"),
        patterns=_patterns(r"查找.+笔记", r"搜索.+内容", r"我之前.*(?:写过|记录过).+", r"知识库里.*(?:有没有|查找|搜索).+"),
        priority=7,
    ),
    IntentRule(
        IntentType.PROJECT_ORGANIZE,
        strong_keywords=("整理项目", "完善项目", "补全项目"),
        keywords=("项目结构", "缺失内容", "项目归档"),
        patterns=_patterns(r"整理一下.+项目", r"补全.+项目", r"检查.+项目.*缺少"),
        priority=7,
    ),
    IntentRule(
        IntentType.PROJECT_SEARCH,
        strong_keywords=("查询项目", "查找项目", "项目进度"),
        keywords=("有哪些项目", "项目状态", "项目内容"),
        patterns=_patterns(r"查看.+项目", r"查询.+项目", r".+项目.*进展"),
        priority=6,
    ),
    IntentRule(
        IntentType.VAULT_ORGANIZE,
        strong_keywords=("整理知识库", "整理仓库", "知识库归类"),
        keywords=("自动分类", "整理文件", "清理知识库"),
        patterns=_patterns(r"整理整个知识库", r"对知识库进行.+分类", r"检查知识库.+混乱"),
        priority=7,
    ),
    IntentRule(
        IntentType.VAULT_SEARCH,
        strong_keywords=("查询知识库", "搜索知识库"),
        keywords=("知识库里", "所有笔记中", "整个仓库"),
        patterns=_patterns(r"知识库里.*(?:查找|搜索|有没有)", r"从所有笔记中查找"),
        priority=6,
    ),
)


@dataclass
class RuleBasedIntentClassifier:
    """不依赖大模型的规则意图识别器。"""

    rules: tuple[IntentRule, ...] = INTENT_RULES
    options: IntentClassifierOptions = field(default_factory=IntentClassifierOptions)
    entity_extractor: EntityExtractor = field(default_factory=EntityExtractor)

    def classify(self, input_text: str) -> IntentResult:
        """识别用户输入的意图、置信度和实体。"""
        raw_text = input_text
        normalized_text = self._normalize_text(input_text)
        if not normalized_text:
            return self._unknown(raw_text)

        candidates = tuple(
            sorted(
                (
                    candidate
                    for rule in self.rules
                    if (candidate := self._evaluate_rule(normalized_text, rule)).score > 0
                ),
                key=lambda candidate: candidate.score,
                reverse=True,
            )
        )
        if not candidates:
            return self._unknown(raw_text)

        best = candidates[0]
        confidence = self._calculate_confidence(best, candidates[1] if len(candidates) > 1 else None)
        intent = IntentType.UNKNOWN if confidence < self.options.unknown_threshold else best.intent
        return IntentResult(
            intent=intent,
            confidence=confidence,
            entities=self.entity_extractor.extract(raw_text, intent),
            raw_text=raw_text,
            requires_confirmation=intent is IntentType.UNKNOWN or confidence < self.options.confirmation_threshold,
            candidates=candidates[:3],
        )

    def _evaluate_rule(self, text: str, rule: IntentRule) -> IntentCandidate:
        score = float(rule.priority)
        matched: list[str] = []

        for keyword in rule.strong_keywords:
            if keyword.lower() in text:
                score += 8
                matched.append(keyword)

        for keyword in rule.keywords:
            if keyword.lower() in text:
                score += 4
                matched.append(keyword)

        for pattern in rule.patterns:
            if pattern.search(text):
                score += 6
                matched.append(pattern.pattern)

        for keyword in rule.negative_keywords:
            if keyword.lower() in text:
                score -= 5

        if not matched:
            score = 0
        return IntentCandidate(rule.intent, max(0, score), tuple(matched))

    def _calculate_confidence(
        self,
        best: IntentCandidate,
        second: IntentCandidate | None,
    ) -> float:
        best_score_confidence = min(best.score / 25, 1)
        if second is None:
            return self._round(best_score_confidence)

        gap = best.score - second.score
        gap_confidence = min(max(gap / 10, 0), 1)
        return self._round(best_score_confidence * 0.7 + gap_confidence * 0.3)

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().lower())

    def _unknown(self, raw_text: str) -> IntentResult:
        return IntentResult(
            intent=IntentType.UNKNOWN,
            confidence=0,
            entities=(),
            raw_text=raw_text,
            requires_confirmation=True,
            candidates=(),
        )

    def _round(self, value: float) -> float:
        return round(value, 3)
