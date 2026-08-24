"""单个 Agent 回合不可变的配置快照。"""

from __future__ import annotations

from dataclasses import dataclass

from agent.protocol.mode import ModeKind
from agent.protocol.op import FileReference, UnattendedPolicy
from agent.skills.loader import Skill

@dataclass(frozen=True, slots=True)
class TurnContext:
    """调度器创建后不再改变，避免运行中的回合读取到后续输入的配置。"""

    submission_id: int
    user_text: str
    system_prompt: str
    file_references: tuple[FileReference, ...] = ()
    skill_snapshot: tuple[Skill, ...] = ()
    mentioned_skills: tuple[Skill, ...] = ()
    mode: ModeKind = ModeKind.DEFAULT
    unattended_policy: UnattendedPolicy | None = None
