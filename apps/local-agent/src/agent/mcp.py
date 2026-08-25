"""Codex 风格的 MCP Host：加载 Server、发现工具并接入现有 ToolRegistry。"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import os
from pathlib import Path
import re
import tomllib
from typing import Any
from urllib.parse import urlparse

import httpx2
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from agent.core.turn.context import TurnContext
from agent.permissions import PermissionRequirement, ToolAccess
from agent.protocol.mode import ModeKind
from agent.tools.types import ToolExecution, ToolSpec


_LOGGER = logging.getLogger(__name__)
_SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_CONFIG_BYTES = 1024 * 1024
_MAX_SERVERS = 32
_MAX_TOOLS = 128
_MAX_INSTRUCTIONS = 8_192
_MAX_TOOL_SPEC_BYTES = 16 * 1_024
_MAX_TOOL_CATALOG_BYTES = 128 * 1_024


class McpApproval(str, Enum):
    AUTO = "auto"
    PROMPT = "prompt"
    WRITES = "writes"
    APPROVE = "approve"


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    name: str
    command: str | None = None
    url: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    env_vars: tuple[str, ...] = ()
    cwd: str | None = None
    bearer_token_env_var: str | None = None
    http_headers: dict[str, str] = field(default_factory=dict)
    env_http_headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    required: bool = False
    supports_parallel_tool_calls: bool = False
    startup_timeout_sec: float = 10.0
    tool_timeout_sec: float = 60.0
    default_tools_approval_mode: McpApproval = McpApproval.AUTO
    enabled_tools: frozenset[str] | None = None
    disabled_tools: frozenset[str] = frozenset()
    tool_approval_modes: dict[str, McpApproval] = field(default_factory=dict)


@dataclass(slots=True)
class _McpConnection:
    config: McpServerConfig
    client: Client
    resources: AsyncExitStack

    async def call_tool(self, name: str, arguments: dict[str, object]) -> Any:
        try:
            async with asyncio.timeout(self.config.tool_timeout_sec):
                return await self.client.call_tool(
                    name,
                    arguments,
                    read_timeout_seconds=self.config.tool_timeout_sec,
                )
        except TimeoutError as exc:
            raise RuntimeError(
                f"MCP 工具 {self.config.name}/{name} 调用超时"
            ) from exc

    async def close(self) -> None:
        await self.resources.aclose()


class McpToolHandler:
    """把一个远端 MCP Tool 包装成项目已有的 ToolHandler。"""

    def __init__(self, connection: _McpConnection, tool: Any) -> None:
        self._connection = connection
        self._remote_name = tool.name
        self._annotations = getattr(tool, "annotations", None)
        self._approval = connection.config.tool_approval_modes.get(
            tool.name, connection.config.default_tools_approval_mode
        )
        read_only = _annotation(self._annotations, "read_only_hint") is True
        self.required_access = (
            ToolAccess.READ_ONLY if read_only else ToolAccess.HOST_EXECUTION
        )
        self.supports_parallel_tool_calls = (
            connection.config.supports_parallel_tool_calls or read_only
        )
        self.spec = ToolSpec(
            name=f"mcp__{connection.config.name}__{tool.name}",
            description=(
                f"[MCP: {connection.config.name}] "
                f"{getattr(tool, 'description', None) or tool.name}"
            )[:4_096],
            parameters=dict(tool.input_schema),
        )

    def permission_requirement(
        self, _arguments: dict[str, object], *, mode: ModeKind = ModeKind.DEFAULT
    ) -> PermissionRequirement:
        del mode
        approval_required = _requires_approval(self._annotations, self._approval)
        reason = (
            f"MCP 工具 {self.spec.name} 将调用外部服务器 "
            f"{self._connection.config.name}。"
            if approval_required
            else None
        )
        return PermissionRequirement(
            self.required_access,
            reason,
            approval_required=approval_required,
        )

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> ToolExecution:
        del granted_access, mode
        result = await self._connection.call_tool(self._remote_name, arguments)
        return _tool_execution(result)


class McpInstructionsContributor:
    def __init__(self, manager: McpManager) -> None:
        self._manager = manager

    async def contribute(self, context: TurnContext) -> str | None:
        del context
        return self._manager.instructions


class McpManager:
    """一个 Agent Runtime 共享的 MCP 连接集。"""

    def __init__(self, config_path: Path | None) -> None:
        self._config_path = config_path
        self._connections: dict[str, _McpConnection] = {}
        self._handlers: tuple[McpToolHandler, ...] = ()
        self._instructions: str | None = None
        self._started = False

    @property
    def instructions(self) -> str | None:
        return self._instructions

    def handlers(self) -> tuple[McpToolHandler, ...]:
        return self._handlers

    async def start(self) -> None:
        if self._started:
            return
        configs = load_mcp_config(self._config_path)
        enabled = tuple(config for config in configs if config.enabled)
        results = await asyncio.gather(
            *(self._open(config) for config in enabled), return_exceptions=True
        )
        required_failures: list[str] = []
        handlers: list[McpToolHandler] = []
        instructions: list[str] = []
        catalog_bytes = 0
        for config, result in zip(enabled, results, strict=True):
            if isinstance(result, BaseException):
                if config.required:
                    required_failures.append(f"{config.name}: {result}")
                else:
                    _LOGGER.warning(
                        "mcp.server_unavailable",
                        extra={"server": config.name, "error_type": type(result).__name__},
                    )
                continue
            connection, tools = result
            self._connections[config.name] = connection
            for tool in tools:
                tool_bytes = _tool_spec_bytes(tool)
                if (
                    len(handlers) >= _MAX_TOOLS
                    or catalog_bytes + tool_bytes > _MAX_TOOL_CATALOG_BYTES
                ):
                    _LOGGER.warning(
                        "mcp.total_tool_limit_reached",
                        extra={
                            "tool_limit": _MAX_TOOLS,
                            "byte_limit": _MAX_TOOL_CATALOG_BYTES,
                        },
                    )
                    break
                handlers.append(McpToolHandler(connection, tool))
                catalog_bytes += tool_bytes
            if connection.client.instructions:
                instructions.append(
                    f"## MCP server: {config.name}\n{connection.client.instructions}"
                )

        if required_failures:
            await self.close()
            raise RuntimeError(
                "必需的 MCP Server 启动失败：" + "; ".join(required_failures)
            )
        self._handlers = tuple(handlers)
        joined = "\n\n".join(instructions)[:_MAX_INSTRUCTIONS]
        self._instructions = (
            f"# MCP Server Instructions\n\n{joined}" if joined else None
        )
        self._started = True

    async def close(self) -> None:
        connections = tuple(reversed(tuple(self._connections.values())))
        self._connections.clear()
        self._handlers = ()
        self._instructions = None
        self._started = False
        for connection in connections:
            try:
                await connection.close()
            except Exception:
                _LOGGER.exception(
                    "mcp.server_close_failed",
                    extra={"server": connection.config.name},
                )

    async def _open(
        self, config: McpServerConfig
    ) -> tuple[_McpConnection, tuple[Any, ...]]:
        async def connect() -> tuple[_McpConnection, tuple[Any, ...]]:
            resources = AsyncExitStack()
            try:
                target: object
                if config.command is not None:
                    child_env = dict(config.env)
                    child_env.update(
                        (name, os.environ[name])
                        for name in config.env_vars
                        if name in os.environ
                    )
                    target = StdioServerParameters(
                        command=config.command,
                        args=list(config.args),
                        env=child_env or None,
                        cwd=config.cwd,
                    )
                else:
                    headers = _http_headers(config)
                    http_client = await resources.enter_async_context(
                        httpx2.AsyncClient(
                            headers=headers,
                            follow_redirects=True,
                            timeout=httpx2.Timeout(30.0, read=300.0),
                        )
                    )
                    target = streamable_http_client(
                        config.url or "", http_client=http_client
                    )
                client = Client(
                    target, mode="auto", read_timeout_seconds=config.tool_timeout_sec
                )
                await resources.enter_async_context(client)
                tools = await _list_tools(client, config)
                return _McpConnection(config, client, resources.pop_all()), tools
            finally:
                await resources.aclose()

        try:
            async with asyncio.timeout(config.startup_timeout_sec):
                return await connect()
        except TimeoutError as exc:
            raise RuntimeError(
                f"MCP Server {config.name} 启动超过 {config.startup_timeout_sec:g} 秒"
            ) from exc


def load_mcp_config(path: Path | None) -> tuple[McpServerConfig, ...]:
    if path is None or not path.exists():
        return ()
    if not path.is_file():
        raise ValueError(f"MCP 配置不是文件: {path}")
    if path.stat().st_size > _MAX_CONFIG_BYTES:
        raise ValueError("MCP 配置不能超过 1 MiB")
    try:
        with path.open("rb") as stream:
            root = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"无法读取 MCP 配置 {path}: {exc}") from exc
    if set(root) - {"mcp_servers"}:
        raise ValueError("MCP 配置顶层只能包含 mcp_servers")
    raw_servers = root.get("mcp_servers", {})
    if not isinstance(raw_servers, dict):
        raise ValueError("mcp_servers 必须是 TOML Table")
    if len(raw_servers) > _MAX_SERVERS:
        raise ValueError(f"MCP Server 不能超过 {_MAX_SERVERS} 个")
    return tuple(_server_config(name, value) for name, value in raw_servers.items())


def _server_config(name: object, value: object) -> McpServerConfig:
    if not isinstance(name, str) or not _SERVER_NAME.fullmatch(name):
        raise ValueError(f"MCP Server 名称无效: {name!r}")
    if not isinstance(value, dict):
        raise ValueError(f"mcp_servers.{name} 必须是 TOML Table")
    allowed = {
        "command",
        "url",
        "args",
        "env",
        "env_vars",
        "cwd",
        "bearer_token_env_var",
        "http_headers",
        "env_http_headers",
        "enabled",
        "required",
        "supports_parallel_tool_calls",
        "startup_timeout_sec",
        "tool_timeout_sec",
        "default_tools_approval_mode",
        "enabled_tools",
        "disabled_tools",
        "tools",
    }
    if unknown := set(value) - allowed:
        raise ValueError(
            f"mcp_servers.{name} 包含未知字段: {', '.join(sorted(unknown))}"
        )
    command = _optional_text(value.get("command"), f"{name}.command")
    url = _optional_text(value.get("url"), f"{name}.url")
    if (command is None) == (url is None):
        raise ValueError(f"mcp_servers.{name} 必须且只能设置 command 或 url")
    if url is not None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"mcp_servers.{name}.url 必须是 HTTP(S) 地址")
        if parsed.username or parsed.password:
            raise ValueError(f"mcp_servers.{name}.url 不能包含用户名或密码")

    tools = value.get("tools", {})
    if not isinstance(tools, dict):
        raise ValueError(f"mcp_servers.{name}.tools 必须是 TOML Table")
    tool_modes: dict[str, McpApproval] = {}
    for tool_name, tool_value in tools.items():
        if not isinstance(tool_name, str) or not _TOOL_NAME.fullmatch(tool_name):
            raise ValueError(f"mcp_servers.{name}.tools 包含无效工具名")
        if not isinstance(tool_value, dict) or set(tool_value) != {"approval_mode"}:
            raise ValueError(
                f"mcp_servers.{name}.tools.{tool_name} 只能设置 approval_mode"
            )
        tool_modes[tool_name] = _approval(
            tool_value["approval_mode"], f"{name}.tools.{tool_name}.approval_mode"
        )

    cwd = _optional_text(value.get("cwd"), f"{name}.cwd")
    if cwd is not None and not Path(cwd).is_dir():
        raise ValueError(f"mcp_servers.{name}.cwd 不是目录")
    enabled_tools = _string_set(value.get("enabled_tools"), f"{name}.enabled_tools")
    return McpServerConfig(
        name=name,
        command=command,
        url=url,
        args=_string_tuple(value.get("args", ()), f"{name}.args"),
        env=_string_map(value.get("env", {}), f"{name}.env"),
        env_vars=_string_tuple(value.get("env_vars", ()), f"{name}.env_vars"),
        cwd=cwd,
        bearer_token_env_var=_optional_text(
            value.get("bearer_token_env_var"), f"{name}.bearer_token_env_var"
        ),
        http_headers=_string_map(
            value.get("http_headers", {}), f"{name}.http_headers"
        ),
        env_http_headers=_string_map(
            value.get("env_http_headers", {}), f"{name}.env_http_headers"
        ),
        enabled=_bool(value.get("enabled", True), f"{name}.enabled"),
        required=_bool(value.get("required", False), f"{name}.required"),
        supports_parallel_tool_calls=_bool(
            value.get("supports_parallel_tool_calls", False),
            f"{name}.supports_parallel_tool_calls",
        ),
        startup_timeout_sec=_positive_number(
            value.get("startup_timeout_sec", 10), f"{name}.startup_timeout_sec"
        ),
        tool_timeout_sec=_positive_number(
            value.get("tool_timeout_sec", 60), f"{name}.tool_timeout_sec"
        ),
        default_tools_approval_mode=_approval(
            value.get("default_tools_approval_mode", "auto"),
            f"{name}.default_tools_approval_mode",
        ),
        enabled_tools=enabled_tools,
        disabled_tools=_string_set(
            value.get("disabled_tools", ()), f"{name}.disabled_tools"
        )
        or frozenset(),
        tool_approval_modes=tool_modes,
    )


async def _list_tools(
    client: Client, config: McpServerConfig
) -> tuple[Any, ...]:
    tools: list[Any] = []
    seen: set[str] = set()
    cursor: str | None = None
    while True:
        result = await client.list_tools(cursor=cursor)
        for tool in result.tools:
            if config.enabled_tools is not None and tool.name not in config.enabled_tools:
                continue
            if tool.name in config.disabled_tools:
                continue
            if tool.name in seen:
                _LOGGER.warning(
                    "mcp.duplicate_tool",
                    extra={"server": config.name, "tool": tool.name},
                )
                continue
            external_name = f"mcp__{config.name}__{tool.name}"
            if not _TOOL_NAME.fullmatch(tool.name) or len(external_name) > 64:
                _LOGGER.warning(
                    "mcp.tool_name_unsupported",
                    extra={"server": config.name, "tool": tool.name},
                )
                continue
            if (
                not isinstance(tool.input_schema, dict)
                or tool.input_schema.get("type") != "object"
            ):
                _LOGGER.warning(
                    "mcp.tool_schema_invalid",
                    extra={"server": config.name, "tool": tool.name},
                )
                continue
            try:
                if _tool_spec_bytes(tool) > _MAX_TOOL_SPEC_BYTES:
                    raise ValueError("tool spec too large")
            except (TypeError, ValueError):
                _LOGGER.warning(
                    "mcp.tool_schema_too_large",
                    extra={"server": config.name, "tool": tool.name},
                )
                continue
            seen.add(tool.name)
            tools.append(tool)
            if len(tools) >= _MAX_TOOLS:
                _LOGGER.warning(
                    "mcp.tool_limit_reached",
                    extra={"server": config.name, "limit": _MAX_TOOLS},
                )
                return tuple(tools)
        cursor = getattr(result, "next_cursor", None)
        if not cursor:
            return tuple(tools)


def _http_headers(config: McpServerConfig) -> dict[str, str]:
    headers = dict(config.http_headers)
    headers.update(
        (header, os.environ[variable])
        for header, variable in config.env_http_headers.items()
        if variable in os.environ
    )
    if (
        config.bearer_token_env_var
        and config.bearer_token_env_var in os.environ
    ):
        headers["Authorization"] = (
            f"Bearer {os.environ[config.bearer_token_env_var]}"
        )
    return headers


def _requires_approval(annotations: object, mode: McpApproval) -> bool:
    if mode is McpApproval.PROMPT:
        return True
    read_only = _annotation(annotations, "read_only_hint") is True
    if mode is McpApproval.WRITES:
        return not read_only
    if mode is McpApproval.APPROVE:
        return False
    destructive = _annotation(annotations, "destructive_hint")
    if destructive is True:
        return True
    if read_only:
        return False
    return destructive is not False or _annotation(annotations, "open_world_hint") is not False


def _annotation(annotations: object, name: str) -> bool | None:
    value = getattr(annotations, name, None)
    return value if type(value) is bool else None


def _tool_execution(result: Any) -> ToolExecution:
    payload = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    content = payload.get("content", [])
    parts: list[str] = []
    image_url = None
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif block.get("type") == "image":
                if (
                    image_url is None
                    and isinstance(block.get("data"), str)
                    and isinstance(block.get("mimeType"), str)
                ):
                    image_url = f"data:{block['mimeType']};base64,{block['data']}"
                else:
                    parts.append("[MCP 返回了额外图片，当前工具结果仅保留第一张。]")
            elif block.get("type") == "audio":
                parts.append("[MCP 返回了音频内容，当前模型工具链不支持音频输入。]")
            else:
                serialized = json.dumps(
                    block, ensure_ascii=False, separators=(",", ":")
                )
                parts.append(serialized[:32_000])
    structured = payload.get("structuredContent")
    if structured is not None:
        parts.append(
            json.dumps(structured, ensure_ascii=False, separators=(",", ":"))
        )
    return ToolExecution(
        "\n".join(parts) or "MCP 工具未返回内容。",
        is_error=payload.get("isError") is True,
        image_url=image_url,
    )


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 4_096:
        raise ValueError(f"mcp_servers.{field_name} 必须是非空字符串")
    return value.strip()


def _tool_spec_bytes(tool: object) -> int:
    return len(
        json.dumps(
            {
                "description": getattr(tool, "description", None),
                "parameters": getattr(tool, "input_schema", None),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) or not item or len(item) > 4_096 for item in value
    ):
        raise ValueError(f"mcp_servers.{field_name} 必须是字符串数组")
    if len(value) > 256:
        raise ValueError(f"mcp_servers.{field_name} 项目过多")
    return tuple(value)


def _string_set(value: object, field_name: str) -> frozenset[str] | None:
    if value is None:
        return None
    return frozenset(_string_tuple(value, field_name))


def _string_map(value: object, field_name: str) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 256 or any(
        not isinstance(key, str)
        or not key
        or len(key) > 512
        or not isinstance(item, str)
        or len(item) > 4_096
        for key, item in value.items()
    ):
        raise ValueError(f"mcp_servers.{field_name} 必须是字符串映射")
    return dict(value)


def _bool(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"mcp_servers.{field_name} 必须是布尔值")
    return value


def _positive_number(value: object, field_name: str) -> float:
    if type(value) not in {int, float} or not 0 < value <= 3_600:
        raise ValueError(f"mcp_servers.{field_name} 必须是 0 到 3600 的数字")
    return float(value)


def _approval(value: object, field_name: str) -> McpApproval:
    try:
        return McpApproval(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"mcp_servers.{field_name} 必须是 auto、prompt、writes 或 approve"
        ) from exc
