"""续接 exec_command 返回的进程。"""

from __future__ import annotations

from agent.permissions import ToolAccess
from agent.protocol.mode import ModeKind
from agent.tools.processes import ProcessManager, validate_yield_time
from agent.tools.types import ToolSpec


class WriteStdinTool:
    """向当前 Session 的既有进程写入字符或轮询输出。"""

    supports_parallel_tool_calls = False
    # process_id 是当前 Session 内的续接能力；这里不能重新解释为一次新的宿主提权请求。
    required_access = ToolAccess.OS_SANDBOX_EXECUTION
    spec = ToolSpec(
        name="write_stdin",
        description=(
            "向 exec_command 返回的 process_id 写入字符并读取新输出。chars 为空时只轮询。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "process_id": {"type": "integer", "minimum": 1},
                "chars": {"type": "string", "description": "原样写入；不会自动添加换行。"},
                "yield_time_ms": {
                    "type": "integer",
                    "minimum": 250,
                    "maximum": 30000,
                    "description": "最多等待的毫秒数；写入默认 250，空轮询默认 5000。",
                },
            },
            "required": ["process_id"],
            "additionalProperties": False,
        },
    )

    def __init__(self, processes: ProcessManager) -> None:
        self._processes = processes

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> str:
        process_id = arguments.get("process_id")
        if (
            not isinstance(process_id, int)
            or isinstance(process_id, bool)
            or process_id < 1
        ):
            raise ValueError("process_id 必须是正整数")
        chars = arguments.get("chars", "")
        if not isinstance(chars, str):
            raise ValueError("chars 必须是字符串")
        default_yield = 250 if chars else 5_000
        yield_time_ms = validate_yield_time(
            arguments.get("yield_time_ms"), default_yield
        )
        result = await self._processes.write_stdin(
            process_id,
            chars,
            yield_time_ms,
            require_read_only=mode is ModeKind.PLAN,
        )
        return result.as_json()
