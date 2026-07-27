"""将可用 Skill 目录作为上下文贡献给模型。"""

from __future__ import annotations

from agent.core.turn.context import TurnContext
from agent.skills.render import render_available_skills


class AvailableSkillsContributor:
    """每轮只暴露名称和描述；Skill 正文由显式注入路径负责。"""

    async def contribute(self, context: TurnContext) -> str | None:
        return render_available_skills(context.skill_snapshot)
