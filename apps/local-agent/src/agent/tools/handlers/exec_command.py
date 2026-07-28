"""启动短命令或可由 write_stdin 续接的后台进程。"""

from __future__ import annotations

import asyncio
import platform

from agent.config.loader import shell_runtime_name
from agent.config.settings import Settings
from agent.permissions import SandboxMode, ToolAccess, command_denial_reason
from agent.protocol.mode import ModeKind
from agent.sandbox import SandboxBackend
from agent.sandbox import windows as windows_sandbox
from agent.tools.processes import (
    ProcessHandle,
    ProcessManager,
    subprocess_environment,
    validate_yield_time,
)
from agent.tools.types import ToolSpec


class _AsyncioProcess:
    """把 asyncio.Process 收窄成 ProcessManager 使用的共同表面。"""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process

    async def read(self, size: int = 65_536) -> bytes:
        assert self._process.stdout is not None
        return await self._process.stdout.read(size)

    async def write(self, data: bytes) -> None:
        if self._process.stdin is None or self._process.stdin.is_closing():
            raise BrokenPipeError("进程 stdin 已关闭")
        self._process.stdin.write(data)
        await self._process.stdin.drain()

    async def wait(self) -> int:
        return await self._process.wait()

    def kill(self) -> None:
        if self._process.returncode is not None:
            return
        try:
            self._process.kill()
        except ProcessLookupError:
            pass


class ExecCommandTool:
    """在指定工作目录执行一条命令。"""

    # shell 命令的读写副作用无法从字符串可靠推断，必须保持互斥。
    supports_parallel_tool_calls = False
    required_access = ToolAccess.HOST_EXECUTION

    spec = ToolSpec(
        name="exec_command",
        description=(
            "在 Agent 工作目录运行 shell 命令。命令在 yield_time_ms 后仍未结束时返回 "
            "process_id，可用 write_stdin 继续交互。读取文本请使用当前 shell 的只读命令"
            "（Windows 推荐 Get-Content/rg，Unix 推荐 cat/rg）；修改文本请使用 apply_patch。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
                "yield_time_ms": {
                    "type": "integer",
                    "minimum": 250,
                    "maximum": 30000,
                    "description": "返回控制权前最多等待的毫秒数，默认 10000。",
                },
                "sandbox_permissions": {
                    "type": "string",
                    "enum": ["use_default", "require_escalated"],
                    "description": (
                        "默认 use_default，在当前 OS 沙盒中执行；只有沙盒阻止完成用户任务时才可使用 "
                        "require_escalated 请求一次宿主执行。"
                    ),
                },
                "justification": {
                    "type": "string",
                    "description": "请求 require_escalated 时向用户说明提权原因。",
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    )

    def __init__(self, settings: Settings, processes: ProcessManager) -> None:
        self._settings = settings
        self._processes = processes
        self._sandbox_available = _native_windows_available(settings.sandbox_backend)
        if (
            settings.sandbox_mode is not SandboxMode.DANGER_FULL_ACCESS
            and self._sandbox_available
        ):
            self.required_access = ToolAccess.OS_SANDBOX_EXECUTION
        base_spec = type(self).spec
        backend = (
            "built-in Windows Restricted Token sandbox"
            if self.required_access is ToolAccess.OS_SANDBOX_EXECUTION
            else "host execution"
        )
        network = (
            settings.sandbox_network.value
            if self.required_access is ToolAccess.OS_SANDBOX_EXECUTION
            else "host"
        )
        self.spec = ToolSpec(
            name=base_spec.name,
            description=(
                f"{base_spec.description}\n\n"
                f"当前执行环境（自动检测）：{platform.system() or 'unknown'}，shell 为 "
                f"{shell_runtime_name()}，sandbox={settings.sandbox_mode.value}，"
                f"network={network}，backend={backend}。"
                "Plan Mode 会强制使用 read-only OS 沙盒，并拒绝宿主提权。"
                "只使用该 shell 的语法，不要使用其他 shell 的语法。"
            ),
            parameters=base_spec.parameters,
        )

    def required_access_for(
        self,
        arguments: dict[str, object],
        *,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> ToolAccess:
        _shell_command(arguments)
        permission, _ = _permission_request(arguments)
        if mode is ModeKind.PLAN and permission != "require_escalated":
            return (
                ToolAccess.OS_SANDBOX_EXECUTION
                if self._sandbox_available
                else ToolAccess.HOST_EXECUTION
            )
        return (
            ToolAccess.HOST_EXECUTION
            if permission == "require_escalated"
            else self.required_access
        )

    def approval_reason(self, arguments: dict[str, object]) -> str | None:
        permission, justification = _permission_request(arguments)
        return justification if permission == "require_escalated" else None

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> str:
        command = _shell_command(arguments)
        yield_time_ms = validate_yield_time(arguments.get("yield_time_ms"), 10_000)
        permission, _ = _permission_request(arguments)
        escalated = permission == "require_escalated"
        if mode is ModeKind.PLAN and escalated:
            raise PermissionError("Plan Mode 不允许请求宿主执行权限")
        if escalated and granted_access is not ToolAccess.HOST_EXECUTION:
            # 参数来自模型；只有统一运行时签发的调用级能力才能越过 OS 沙盒。
            raise PermissionError("本次 exec_command 调用尚未获得宿主执行批准")

        async def spawn() -> ProcessHandle:
            effective_sandbox_mode = (
                SandboxMode.READ_ONLY
                if mode is ModeKind.PLAN
                else self._settings.sandbox_mode
            )
            if (
                effective_sandbox_mode is SandboxMode.DANGER_FULL_ACCESS
                or escalated
            ):
                process = await asyncio.create_subprocess_shell(
                    command,
                    cwd=str(self._settings.workspace),
                    # 子进程不应继承 Agent 自己的模型凭据，即使用户批准了宿主执行。
                    env=subprocess_environment(),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                return _AsyncioProcess(process)
            if not self._sandbox_available:
                raise RuntimeError("当前平台没有可用的 OS 沙盒后端")
            self._settings.sandbox_state_dir.mkdir(parents=True, exist_ok=True)
            return await windows_sandbox.spawn(
                command,
                cwd=self._settings.workspace,
                state_dir=self._settings.sandbox_state_dir,
                mode=effective_sandbox_mode,
                network=self._settings.sandbox_network,
                environment=subprocess_environment(),
            )

        read_only = (
            mode is ModeKind.PLAN
            or self._settings.sandbox_mode is SandboxMode.READ_ONLY
        ) and not escalated
        result = await self._processes.exec_command(
            spawn, yield_time_ms, read_only=read_only
        )
        return result.as_json()


def _permission_request(arguments: dict[str, object]) -> tuple[str, str | None]:
    permission = arguments.get("sandbox_permissions", "use_default")
    if not isinstance(permission, str) or permission not in {
        "use_default",
        "require_escalated",
    }:
        raise ValueError("sandbox_permissions 必须是 use_default 或 require_escalated")
    justification = arguments.get("justification")
    if justification is not None and not isinstance(justification, str):
        raise ValueError("justification 必须是字符串")
    if permission == "require_escalated" and (
        not isinstance(justification, str) or not justification.strip()
    ):
        raise ValueError("require_escalated 必须提供非空 justification")
    return permission, justification.strip() if isinstance(justification, str) else None


def _shell_command(arguments: dict[str, object]) -> str:
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command 必须是非空字符串")
    command = command.strip()
    if reason := command_denial_reason(command):
        # 权限需求解析发生在工具 dispatch 之前；ValueError 会被统一转换为可恢复的工具错误。
        raise ValueError(f"命令安全策略拒绝：{reason}")
    return command


def _native_windows_available(backend: SandboxBackend) -> bool:
    return backend is not SandboxBackend.DISABLED and windows_sandbox.is_available()
