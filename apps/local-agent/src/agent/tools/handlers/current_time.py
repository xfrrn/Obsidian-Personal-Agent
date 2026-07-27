"""返回模型可查询的当前 UTC 时间。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json

from agent.permissions import ToolAccess
from agent.protocol.mode import ModeKind
from agent.tools.types import ToolSpec


class CurrentTimeTool:
    """返回可复现格式的 UTC 时间。"""

    supports_parallel_tool_calls = True
    required_access = ToolAccess.READ_ONLY
    spec = ToolSpec(
        name="current_time",
        description="返回当前 UTC 时间。",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
    )

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> str:
        if arguments:
            raise ValueError("该工具不接受参数")
        current = self._now().astimezone(timezone.utc)
        return json.dumps(
            {"current_time": current.strftime("%Y-%m-%d %H:%M:%S UTC")},
            ensure_ascii=False,
        )
