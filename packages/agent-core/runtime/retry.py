"""Runtime 重试策略。"""

from __future__ import annotations

from dataclasses import dataclass

from exceptions import AgentValidationError


@dataclass(frozen=True)
class RetryPolicy:
    """第一版只保存配置，不在 Runtime 中自动重试。"""

    max_attempts: int = 1
    backoff_seconds: float = 0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise AgentValidationError("max_attempts must be at least 1")
        if self.backoff_seconds < 0:
            raise AgentValidationError("backoff_seconds cannot be negative")
