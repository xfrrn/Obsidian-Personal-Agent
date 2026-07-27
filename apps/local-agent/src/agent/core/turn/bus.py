"""TurnEvent 的同步分发与旁路 mailbox。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from agent.core.turn.events import TurnEvent, is_reliable_event


DEFAULT_MAILBOX_CAPACITY = 512


class MailboxClosed(RuntimeError):
    """watch 所属 Session 已关闭。"""


class Mailbox:
    """一个 watcher 独占的有界队列；慢观察者不能阻塞 turn loop。"""

    _CLOSED = object()

    def __init__(self, capacity: int = DEFAULT_MAILBOX_CAPACITY) -> None:
        if capacity <= 0:
            raise ValueError("mailbox capacity 必须为正数")
        self._items: asyncio.Queue[TurnEvent | object] = asyncio.Queue(maxsize=capacity)
        self._closed = False
        self.dropped = 0

    def try_send(self, event: TurnEvent) -> bool:
        """非阻塞投递；旁路事件丢失不能反向拖慢 Agent。"""

        if self._closed:
            return False
        try:
            self._items.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1
            return False
        return True

    async def receive(self) -> TurnEvent:
        if self._closed and self._items.empty():
            raise MailboxClosed("mailbox 已关闭")
        return self._consume(await self._items.get())

    def try_receive(self) -> TurnEvent | None:
        """非阻塞读取，供入口在完整答复前清空已到达的流式增量。"""

        if self._closed and self._items.empty():
            raise MailboxClosed("mailbox 已关闭")
        try:
            item = self._items.get_nowait()
        except asyncio.QueueEmpty:
            return None
        return self._consume(item)

    def _consume(self, item: TurnEvent | object) -> TurnEvent:
        if item is self._CLOSED:
            # 让同一 mailbox 的其余等待者也能立即退出。
            self._items.put_nowait(self._CLOSED)
            raise MailboxClosed("mailbox 已关闭")
        if self._closed and self._items.empty():
            # close() 保留已入队事件；最后一个消费者负责唤醒剩余等待者。
            self._items.put_nowait(self._CLOSED)
        return item  # type: ignore[return-value]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # 关闭不丢弃已经投递的事件，尤其是 RuntimeShutdown；满队列会在最后一项被取走时补入哨兵。
        if self._items.empty():
            self._items.put_nowait(self._CLOSED)


TurnEventListener = Callable[[TurnEvent], None]


class TurnEventBus:
    """可靠事件同步映射；所有事件同时镜像给可丢弃的旁路 mailbox。"""

    def __init__(self) -> None:
        self._listeners: list[tuple[TurnEventListener, bool]] = []
        self._mailboxes: list[Mailbox] = []
        self._closed = False

    def subscribe(self, listener: TurnEventListener, *, include_deltas: bool = False) -> None:
        """注册可靠映射；需要按 turn 转发 SSE 时可显式接收增量。"""

        if self._closed:
            return
        self._listeners.append((listener, include_deltas))

    def watch(self, capacity: int = DEFAULT_MAILBOX_CAPACITY) -> Mailbox:
        mailbox = Mailbox(capacity)
        if self._closed:
            mailbox.close()
            return mailbox
        self._mailboxes.append(mailbox)
        return mailbox

    def emit(self, event: TurnEvent) -> None:
        if self._closed:
            return
        for listener, include_deltas in tuple(self._listeners):
            if is_reliable_event(event) or include_deltas:
                listener(event)
        for mailbox in tuple(self._mailboxes):
            mailbox.try_send(event)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for mailbox in self._mailboxes:
            mailbox.close()
        self._mailboxes.clear()
        self._listeners.clear()
