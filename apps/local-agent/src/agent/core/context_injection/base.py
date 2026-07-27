"""回合上下文贡献者协议。"""

from __future__ import annotations

from typing import Protocol

from agent.core.turn.context import TurnContext


class ContextContributor(Protocol):
    """将一段可选系统上下文贡献给指定回合。"""

    async def contribute(self, context: TurnContext) -> str | None: ...
