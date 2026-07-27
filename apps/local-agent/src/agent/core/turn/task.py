"""可由 Session 调度的回合任务基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod


class SessionTask(ABC):
    """将“如何运行一个回合”与“何时调度、取消它”分开。"""

    @abstractmethod
    async def run(self) -> None:
        """完成一个回合，取消时必须传播 ``CancelledError``。"""
