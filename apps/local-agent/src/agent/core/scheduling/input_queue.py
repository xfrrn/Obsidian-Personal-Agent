"""Session 内部等待调度的用户输入队列。"""

from __future__ import annotations

import asyncio

from agent.protocol.submission import Submission


class InputQueue:
    """将 Handle 的公共操作队列与回合调度器的内部输入边界隔离。"""

    def __init__(self) -> None:
        self._submissions: asyncio.Queue[Submission] = asyncio.Queue()

    async def put(self, submission: Submission) -> None:
        await self._submissions.put(submission)

    async def get(self) -> Submission:
        return await self._submissions.get()
