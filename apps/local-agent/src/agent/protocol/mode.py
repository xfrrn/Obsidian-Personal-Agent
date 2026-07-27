"""Agent 对外可选的协作模式。"""

from __future__ import annotations

from enum import Enum


class ModeKind(str, Enum):
    """只保留当前真正支持的两种行为，避免为废弃模式保留分支。"""

    DEFAULT = "default"
    PLAN = "plan"

    @classmethod
    def parse(cls, value: str) -> "ModeKind":
        try:
            return cls(value.strip().lower())
        except (AttributeError, ValueError) as exc:
            choices = ", ".join(mode.value for mode in cls)
            raise ValueError(f"mode 必须是: {choices}") from exc
