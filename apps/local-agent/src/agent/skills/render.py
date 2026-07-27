"""将 Skill 元数据渲染为模型可见的精简目录。"""

from __future__ import annotations

from agent.skills.loader import Skill


def render_available_skills(skills: tuple[Skill, ...]) -> str | None:
    """不包含任何正文；模型只在用户明确写出 ``$name`` 后才取得完整说明。"""

    if not skills:
        return None
    entries = "\n".join(f"- ${skill.name}: {skill.description}" for skill in skills)
    return "可用 Skills（用户显式提及 $skill-name 时读取其工作流说明）：\n" + entries
