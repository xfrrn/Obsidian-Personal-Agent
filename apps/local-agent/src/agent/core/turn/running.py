"""已启动回合的运行时追踪与有序取消。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass

from agent.core.turn.context import TurnContext


@dataclass(slots=True)
class RunningTask:
    """绑定回合上下文和 asyncio Task，供调度器安全地替换活动回合。"""

    context: TurnContext
    task: asyncio.Task[None]

    async def cancel_and_wait(self) -> None:
        """等待旧回合停止，保证它的中断事件不会混入新回合事件之前。"""

        if not self.task.done():
            self.task.cancel()
        with suppress(asyncio.CancelledError):
            await self.task
