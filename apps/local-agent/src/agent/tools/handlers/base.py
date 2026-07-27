"""具体工具处理器的最小接口。"""

from __future__ import annotations

from typing import Protocol

from agent.permissions import ToolAccess
from agent.protocol.mode import ModeKind
from agent.tools.types import ToolExecution, ToolExecutionContext, ToolSpec


class ToolHandler(Protocol):
    """Registry 只依赖此协议，不需要知道 shell 等具体实现。"""

    spec: ToolSpec
    required_access: ToolAccess
    # 未声明的处理器按不支持并行处理，避免新工具在未经审查时与写操作竞争。
    supports_parallel_tool_calls: bool

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
        context: ToolExecutionContext | None = None,
    ) -> str | ToolExecution: ...
