"""通过官方 Obsidian CLI 列出或执行命令面板命令。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil

from agent.config.settings import Settings
from agent.permissions import ToolAccess
from agent.protocol.mode import ModeKind
from agent.tools.processes import bounded_output, subprocess_environment
from agent.tools.types import ToolExecution, ToolSpec


class ObsidianCommandTool:
    """把固定的 Obsidian CLI 调用暴露为受权限控制的模型工具。"""

    supports_parallel_tool_calls = False
    required_access = ToolAccess.READ_ONLY
    spec = ToolSpec(
        name="obsidian_command",
        description=(
            "通过官方 Obsidian CLI 列出或执行 Obsidian 命令面板中的命令。"
            "action=list 时可用 filter 按命令 ID 前缀筛选；action=execute 时必须提供 command_id。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "execute"]},
                "filter": {
                    "type": "string",
                    "description": "list 时可选的命令 ID 前缀。",
                },
                "command_id": {
                    "type": "string",
                    "description": "execute 时必填的 Obsidian 命令 ID。",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    )

    def __init__(self, settings: Settings) -> None:
        self._workspace = settings.workspace
        self._timeout_seconds = settings.request_timeout_seconds

    def required_access_for(
        self,
        arguments: dict[str, object],
        *,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> ToolAccess:
        action, _ = _parse_arguments(arguments)
        return (
            ToolAccess.HOST_EXECUTION
            if action == "execute"
            else ToolAccess.READ_ONLY
        )

    def approval_reason(self, arguments: dict[str, object]) -> str | None:
        action, value = _parse_arguments(arguments)
        return f"即将在 Obsidian 中执行命令：{value}" if action == "execute" else None

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> ToolExecution:
        action, value = _parse_arguments(arguments)
        if action == "execute" and granted_access is not ToolAccess.HOST_EXECUTION:
            raise PermissionError("本次 Obsidian 命令尚未获得宿主执行权限")

        executable = _find_obsidian_cli()
        if executable is None:
            return ToolExecution(
                "未找到 Obsidian CLI。请安装 Obsidian 1.12.7+，在“设置 → 常规”中启用"
                "“命令行界面”，重启终端后运行 `obsidian version` 验证。",
                is_error=True,
            )

        cli_arguments = _cli_arguments(action, value)
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                *cli_arguments,
                cwd=str(self._workspace),
                env=subprocess_environment(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            return ToolExecution(f"无法启动 Obsidian CLI：{exc}", is_error=True)

        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=self._timeout_seconds
            )
        except TimeoutError:
            await _stop_process(process)
            return ToolExecution(
                f"Obsidian CLI 超过 {self._timeout_seconds:g} 秒未完成，已终止。",
                is_error=True,
            )
        except asyncio.CancelledError:
            await _stop_process(process)
            raise

        output, omitted_bytes = bounded_output(stdout or b"")
        result = json.dumps(
            {
                "action": action,
                "command": " ".join(("obsidian", *cli_arguments)),
                "exit_code": process.returncode,
                "output": output,
                "omitted_bytes": omitted_bytes,
            },
            ensure_ascii=False,
        )
        return ToolExecution(result, is_error=process.returncode != 0)


def _parse_arguments(arguments: dict[str, object]) -> tuple[str, str | None]:
    unknown = set(arguments) - {"action", "filter", "command_id"}
    if unknown:
        raise ValueError(f"包含未知字段: {', '.join(sorted(unknown))}")
    action = arguments.get("action")
    if action not in {"list", "execute"}:
        raise ValueError("action 必须是 list 或 execute")

    filter_value = _optional_text(arguments.get("filter"), "filter")
    command_id = _optional_text(arguments.get("command_id"), "command_id")
    if action == "list":
        if command_id is not None:
            raise ValueError("action=list 时不能提供 command_id")
        return action, filter_value
    if filter_value is not None:
        raise ValueError("action=execute 时不能提供 filter")
    if command_id is None:
        raise ValueError("action=execute 时必须提供 command_id")
    return action, command_id


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    resolved = value.strip()
    if len(resolved) > 500:
        raise ValueError(f"{name} 不能超过 500 个字符")
    return resolved


def _cli_arguments(action: str, value: str | None) -> tuple[str, ...]:
    if action == "list":
        return ("commands",) if value is None else ("commands", f"filter={value}")
    assert value is not None
    return "command", f"id={value}"


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()
    await process.wait()


def _find_obsidian_cli() -> str | None:
    """读取持久化 PATH，兼容 Agent 启动后才启用 CLI 的 Windows 会话。"""

    executable = shutil.which("obsidian")
    if executable is not None or os.name != "nt":
        return executable
    registered_path = _windows_registered_path()
    return shutil.which("obsidian", path=registered_path) if registered_path else None


def _windows_registered_path() -> str:
    import winreg

    values = [os.environ.get("PATH", "")]
    keys = (
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    )
    for root, name in keys:
        try:
            with winreg.OpenKey(root, name) as key:
                value, _ = winreg.QueryValueEx(key, "Path")
        except OSError:
            continue
        if isinstance(value, str) and value:
            values.append(os.path.expandvars(value))
    return ";".join(values)
