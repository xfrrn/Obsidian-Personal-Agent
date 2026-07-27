"""Skill 发现和快照缓存服务。"""

from __future__ import annotations

from pathlib import Path

from agent.skills.loader import Skill, discover_skills, skill_fingerprint


class SkillsService:
    """缓存当前根目录的元数据，文件变化后下一回合自动生成新快照。"""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._fingerprint: tuple[tuple[str, int, int], ...] | None = None
        self._skills: tuple[Skill, ...] = ()

    def snapshot(self) -> tuple[Skill, ...]:
        """返回本回合应使用的不可变 Skill 视图。"""

        fingerprint = skill_fingerprint(self._root)
        if fingerprint != self._fingerprint:
            self._skills = discover_skills(self._root)
            self._fingerprint = fingerprint
        return self._skills
