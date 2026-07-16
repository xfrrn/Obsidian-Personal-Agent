"""基于规则和关键词评分的意图识别器。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
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


INTENT_SEGMENT_SPLITTER = re.compile(
    r"\s*(?:[，,；;。.!！？\n]+|(?:并且|然后|同时|接着|以及|再|并))\s*"
)


INTENT_RULES: tuple[IntentRule, ...] = (
    IntentRule(
        IntentType.CHAT_GENERAL,
        strong_keywords=("你好", "您好", "hello", "hi"),
        keywords=("在吗", "谢谢", "感谢"),
        patterns=_patterns(r"^(你好|您好|hello|hi)[!！。,.，\s]*$"),
        priority=8,
    ),
    IntentRule(
        IntentType.VAULT_HEALTH,
        strong_keywords=("知识库健康检查", "检查知识库健康", "知识库体检", "健康检查"),
        keywords=("失效链接", "孤立笔记", "知识库问题"),
        patterns=_patterns(r"检查(?:整个)?知识库.*(?:健康|问题|规范)", r"生成.+健康报告"),
        priority=11,
    ),
    IntentRule(
        IntentType.NOTE_INSPECT,
        strong_keywords=(
            "检查当前笔记",
            "笔记规范检查",
            "分析当前笔记",
            "应该放在哪里",
            "适合放在哪里",
            "放哪个目录",
        ),
        keywords=("检查笔记", "缺失字段", "链接检查", "归档建议"),
        patterns=_patterns(
            r"检查.+笔记.*(?:规范|问题|缺少)",
            r"这篇笔记.*(?:有问题|缺什么)",
            r"(?:笔记|文档).*(?:应该|适合|建议).*(?:放在|放到|归档到|哪个目录|哪里)",
        ),
        priority=10,
    ),
    IntentRule(
        IntentType.PROJECT_ANALYZE,
        strong_keywords=("分析项目", "项目健康检查", "项目缺少什么", "项目还缺什么"),
        keywords=("项目缺失文档", "项目总览", "项目未更新"),
        patterns=_patterns(r"分析一下.+项目", r".+项目.*(?:还缺|缺少).+", r"检查.+项目.*(?:状态|健康)"),
        priority=11,
    ),
    IntentRule(
        IntentType.NOTE_RELATED,
        strong_keywords=("查找关联笔记", "推荐相关笔记", "有哪些关联笔记"),
        keywords=("双向链接", "内容关联", "相关内容推荐"),
        patterns=_patterns(r"(?:查找|推荐).+(?:关联|相关)笔记", r"和当前笔记.*相关"),
        priority=10,
    ),
    IntentRule(
        IntentType.NOTE_DUPLICATES,
        strong_keywords=("查找重复笔记", "检查重复笔记", "检查重复内容", "检测相似笔记"),
        keywords=("重复笔记", "相似内容", "内容重复"),
        patterns=_patterns(r"(?:查找|检查|检测).+(?:重复|相似).+笔记"),
        priority=10,
    ),
    IntentRule(
        IntentType.TAG_LIST,
        strong_keywords=("列出标签", "标签统计", "查看标签体系"),
        keywords=("有哪些标签", "标签使用次数", "废弃标签"),
        patterns=_patterns(r"(?:列出|统计|查看).+标签"),
        priority=10,
    ),
    IntentRule(
        IntentType.RULE_LIST,
        strong_keywords=("列出规则", "列出知识库规则", "查看知识库规则", "有哪些规则"),
        keywords=("规则配置", "整理规则", "命名规则"),
        patterns=_patterns(r"(?:列出|查看).+(?:管理|整理|知识库)规则"),
        priority=10,
    ),
    IntentRule(
        IntentType.RULE_EVALUATE,
        strong_keywords=("试运行规则", "检查规则结果", "评估规则"),
        keywords=("规则检查", "规则会怎么处理"),
        patterns=_patterns(r"(?:试运行|评估|检查).+规则"),
        priority=10,
    ),
    IntentRule(
        IntentType.TASK_EXTRACT,
        strong_keywords=("提取潜在任务", "从笔记提取任务", "找出待办"),
        keywords=("潜在任务", "任务候选", "哪些内容要做"),
        patterns=_patterns(r"从.+笔记.*提取.+任务", r"找出.+(?:待办|任务候选)"),
        priority=10,
    ),
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
        strong_keywords=("归档笔记", "移动笔记", "整理到目录", "删除笔记", "移入废纸篓", "移入回收站"),
        keywords=("归档", "移动到", "放到文件夹"),
        patterns=_patterns(r"把.+移动到.+", r"把.+归档到.+", r"将.+放入.+目录", r"(?:删除|移除).+(?:笔记|文档)"),
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
        strong_keywords=(
            "整理知识库",
            "整理仓库",
            "知识库归类",
            "创建目录",
            "新建目录",
            "创建文件夹",
            "新建文件夹",
            "删除空目录",
            "删除空文件夹",
        ),
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

    def classify_multi(self, input_text: str) -> tuple[IntentResult, ...]:
        """按自然分隔符识别一句话里的多个顺序意图。"""
        segments = self._segments(input_text)
        if len(segments) < 2:
            return (self.classify(input_text),)

        results = tuple(
            replace(result, raw_text=input_text)
            for segment in segments
            if (result := self.classify(segment)).intent is not IntentType.UNKNOWN
        )
        return results or (self.classify(input_text),)

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

    def _segments(self, text: str) -> tuple[str, ...]:
        segments: list[str] = []
        start = 0
        for match in INTENT_SEGMENT_SPLITTER.finditer(text):
            segment = text[start:match.start()].strip()
            if segment:
                segments.append(segment)
            start = match.end()
        tail = text[start:].strip()
        if tail:
            segments.append(tail)
        return tuple(segments)

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
