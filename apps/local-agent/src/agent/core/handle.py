"""入口层持有的双向队列包装。"""

from __future__ import annotations

import asyncio
import itertools

from agent.core.turn.bus import DEFAULT_MAILBOX_CAPACITY, Mailbox, TurnEventBus
from agent.protocol.op import Op, Shutdown
from agent.protocol.submission import Submission


class AgentHandle:
    """将输入操作与输出事件隔离开。

    入口层通过 ``submit`` 提交操作，并从 ``turn_events.watch()`` 取得独占 mailbox；
    因此不会直接接触 Session 的可变状态。
    无界队列是 MVP 的刻意取舍：交互式终端不应因短暂的模型延迟丢失用户输入。
    """

    def __init__(self) -> None:
        self._operations: asyncio.Queue[Submission] = asyncio.Queue()
        self.turn_events = TurnEventBus()
        self._submission_ids = itertools.count(1)

    def submit(self, op: Op) -> int:
        """非阻塞地提交操作，并返回可用于追踪的 submission id。"""

        submission = Submission(next(self._submission_ids), op)
        self._operations.put_nowait(submission)
        return submission.id

    async def next_submission(self) -> Submission:
        """供核心事件循环读取下一条操作。"""

        return await self._operations.get()

    def watch(self, capacity: int = DEFAULT_MAILBOX_CAPACITY) -> Mailbox:
        """订阅本 Agent 的内部回合事件；应在提交操作前调用以免错过开头事件。"""

        return self.turn_events.watch(capacity)

    def shutdown(self) -> int:
        """提交有序关停请求，而不是从入口层直接取消内部任务。"""

        return self.submit(Shutdown())
