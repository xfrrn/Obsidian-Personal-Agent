"""Agent Core 基础异常。"""

from __future__ import annotations

from typing import Mapping


class AgentCoreError(Exception):
    """Agent Core 所有业务异常的基类。"""

    code = "agent_core.error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.code
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, object]:
        """转换成适合 API 返回或日志记录的结构。"""
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class AgentValidationError(AgentCoreError, ValueError):
    """输入、配置或定义不合法。"""

    code = "agent_core.validation_error"


class ConversationError(AgentValidationError):
    """对话上下文相关错误。"""

    code = "agent_core.conversation_error"


class IntentClassificationError(AgentCoreError):
    """意图识别失败。"""

    code = "agent_core.intent_classification_error"


class PlanValidationError(AgentCoreError):
    """计划校验失败。"""

    code = "agent_core.plan_validation_error"


class ToolRegistrationError(AgentValidationError):
    """工具注册失败。"""

    code = "agent_core.tool_registration_error"


class ToolNotFoundError(AgentCoreError, KeyError):
    """工具不存在。"""

    code = "agent_core.tool_not_found"


class ToolPermissionDeniedError(AgentCoreError, PermissionError):
    """工具权限不足。"""

    code = "agent_core.tool_permission_denied"


class AgentCancelledError(AgentCoreError, RuntimeError):
    """Agent 运行已取消。"""

    code = "agent_core.cancelled"
