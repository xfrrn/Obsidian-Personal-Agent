"""基于正则的轻量实体提取器。"""

from __future__ import annotations

import re
from typing import Iterable

from .intent_types import IntentEntity, IntentEntityType, IntentType


class EntityExtractor:
    """从用户输入中提取日期、标签、引用值和目标名称。"""

    _DATE_PATTERNS = tuple(
        re.compile(pattern)
        for pattern in (
            r"今天",
            r"明天",
            r"后天",
            r"本周",
            r"下周",
            r"这个月",
            r"下个月",
            r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?",
            r"\d{1,2}月\d{1,2}日",
            r"周[一二三四五六日天]",
            r"星期[一二三四五六日天]",
        )
    )

    _TARGET_PATTERNS: dict[IntentType, tuple[IntentEntityType, tuple[re.Pattern[str], ...]]] = {
        IntentType.TASK_COMPLETE: (
            IntentEntityType.TASK_NAME,
            (
                re.compile(r"完成(?:任务)?[：:\s]*([^，。]+)"),
                re.compile(r"把[“\"'《【]?(.+?)[”\"'》】]?(?:这个)?任务.*完成"),
                re.compile(r"将[“\"'《【]?(.+?)[”\"'》】]?.*标记为完成"),
            ),
        ),
        IntentType.TASK_CREATE: (
            IntentEntityType.TASK_NAME,
            (
                re.compile(r"创建(?:一个)?任务[：:\s]*([^，。]+)"),
                re.compile(r"新建(?:一个)?任务[：:\s]*([^，。]+)"),
                re.compile(r"提醒我[：:\s]*([^，。]+)"),
                re.compile(r"待办[：:\s]*([^，。]+)"),
            ),
        ),
        IntentType.NOTE_SEARCH: (
            IntentEntityType.KEYWORD,
            (
                re.compile(r"搜索(?:关于)?[“\"'《【]?(.+?)[”\"'》】]?(?:的)?(?:笔记|内容)"),
                re.compile(r"查找(?:关于)?[“\"'《【]?(.+?)[”\"'》】]?(?:的)?(?:笔记|内容)"),
                re.compile(r"有没有(?:关于)?[“\"'《【]?(.+?)[”\"'》】]?(?:的)?记录"),
            ),
        ),
        IntentType.NOTE_OPTIMIZE: (
            IntentEntityType.NOTE_NAME,
            (
                re.compile(r"优化[“\"'《【]?(.+?)[”\"'》】]?(?:这篇)?笔记"),
                re.compile(r"润色[“\"'《【]?(.+?)[”\"'》】]?(?:这篇)?笔记"),
            ),
        ),
        IntentType.NOTE_ARCHIVE: (
            IntentEntityType.FOLDER,
            (
                re.compile(r"移动到[“\"'《【]?(.+?)[”\"'》】]?(?:文件夹|目录)?$"),
                re.compile(r"归档到[“\"'《【]?(.+?)[”\"'》】]?(?:文件夹|目录)?$"),
                re.compile(r"放到[“\"'《【]?(.+?)[”\"'》】]?(?:文件夹|目录)?$"),
            ),
        ),
        IntentType.PROJECT_SEARCH: (
            IntentEntityType.PROJECT_NAME,
            (
                re.compile(r"查看[“\"'《【]?(.+?)[”\"'》】]?(?:项目)?(?:的)?(?:进度|内容|状态)"),
                re.compile(r"查询[“\"'《【]?(.+?)[”\"'》】]?(?:项目)"),
            ),
        ),
    }

    def extract(self, text: str, intent: IntentType) -> tuple[IntentEntity, ...]:
        """根据识别出的意图提取实体。"""
        entities = [
            *self._extract_dates(text),
            *self._extract_tags(text),
            *self._extract_quoted_values(text),
            *self._extract_target_name(text, intent),
        ]
        return tuple(self._remove_duplicates(entities))

    def _extract_dates(self, text: str) -> list[IntentEntity]:
        return self._extract_by_patterns(text, self._DATE_PATTERNS, IntentEntityType.DATE)

    def _extract_tags(self, text: str) -> list[IntentEntity]:
        entities: list[IntentEntity] = []
        for match in re.finditer(r"#([\w\-/\u4e00-\u9fff]+)", text):
            entities.append(
                IntentEntity(
                    IntentEntityType.TAG,
                    match.group(1),
                    match.start(),
                    match.end(),
                )
            )
        return entities

    def _extract_quoted_values(self, text: str) -> list[IntentEntity]:
        entities: list[IntentEntity] = []
        for match in re.finditer(r"[“\"'《【]([^”\"'》】]+)[”\"'》】]", text):
            value = match.group(1).strip()
            if value:
                entities.append(IntentEntity(IntentEntityType.KEYWORD, value, match.start(), match.end()))
        return entities

    def _extract_target_name(self, text: str, intent: IntentType) -> list[IntentEntity]:
        config = self._TARGET_PATTERNS.get(intent)
        if not config:
            return []

        entity_type, patterns = config
        for pattern in patterns:
            match = pattern.search(text)
            value = match.group(1).strip() if match else ""
            if value:
                return [IntentEntity(entity_type, value, match.start(), match.end())]
        return []

    def _extract_by_patterns(
        self,
        text: str,
        patterns: Iterable[re.Pattern[str]],
        entity_type: IntentEntityType,
    ) -> list[IntentEntity]:
        entities: list[IntentEntity] = []
        for pattern in patterns:
            for match in pattern.finditer(text):
                entities.append(IntentEntity(entity_type, match.group(0), match.start(), match.end()))
        return entities

    def _remove_duplicates(self, entities: Iterable[IntentEntity]) -> list[IntentEntity]:
        seen: set[tuple[IntentEntityType, str]] = set()
        result: list[IntentEntity] = []
        for entity in entities:
            key = (entity.type, entity.value)
            if key in seen:
                continue
            seen.add(key)
            result.append(entity)
        return result
