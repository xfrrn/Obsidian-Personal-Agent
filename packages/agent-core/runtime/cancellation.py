"""Runtime 取消标记。"""

from __future__ import annotations

from exceptions import AgentCancelledError


class CancellationToken:
    """轻量取消标记。"""

    def __init__(self) -> None:
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        """是否已经取消。"""
        return self._cancelled

    def cancel(self) -> None:
        """标记为取消。"""
        self._cancelled = True

    def throw_if_cancelled(self) -> None:
        """已取消时抛出异常。"""
        if self._cancelled:
            raise AgentCancelledError("agent run cancelled")
