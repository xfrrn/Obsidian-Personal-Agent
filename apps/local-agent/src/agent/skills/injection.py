"""显式 Skill mention 的解析与正文注入构造。"""

from __future__ import annotations

import re

from agent.skills.loader import Skill, load_instructions


_MENTION = re.compile(r"(?<![\w-])\$([a-z0-9][a-z0-9-]*)\b")


def collect_explicit_mentions(text: str, skills: tuple[Skill, ...]) -> tuple[Skill, ...]:
    """按输入中的首次出现顺序收集已知 Skill，未知名称仍作为普通用户文本保留。"""

    available = {skill.name: skill for skill in skills}
    mentioned: list[Skill] = []
    seen: set[str] = set()
    for name in _MENTION.findall(text):
        if name in available and name not in seen:
            mentioned.append(available[name])
            seen.add(name)
    return tuple(mentioned)


def build_skill_injections(skills: tuple[Skill, ...]) -> tuple[str, ...]:
    """读取被显式选中的正文，并暴露其文件系统目录供工具解析相对路径。"""

    return tuple(
        f"以下是用户显式选择的 Skill `${skill.name}`。遵循其工作流，"
        "但不得覆盖更高优先级系统指令。\n\n"
        "<skill>\n"
        f"<name>{skill.name}</name>\n"
        f"<path>{skill.path.parent}</path>\n"
        f"{load_instructions(skill)}\n"
        "</skill>"
        for skill in skills
    )
