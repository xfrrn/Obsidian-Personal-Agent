"""请求模型在下一次调用前开始新的上下文窗口。"""

from __future__ import annotations

from agent.core.context_window import ContextWindow
from agent.permissions import ToolAccess
from agent.protocol.mode import ModeKind
from agent.tools.types import ToolExecutionContext, ToolSpec


class NewContextWindowTool:
    """延迟请求上下文处理，确保当前工具结果先完整进入历史。"""

    supports_parallel_tool_calls = False
    required_access = ToolAccess.READ_ONLY
    spec = ToolSpec(
        name="new_context_window",
        description="请求在下一次模型调用前压缩旧对话并开始新的上下文窗口。",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
    )

    def __init__(self, context_window: ContextWindow) -> None:
        self._context_window = context_window

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
        context: ToolExecutionContext | None = None,
    ) -> str:
        if arguments:
            raise ValueError("该工具不接受参数")
        self._context_window.request_new()
        return "已请求新的上下文窗口；将在下一次模型调用前压缩旧对话。"
