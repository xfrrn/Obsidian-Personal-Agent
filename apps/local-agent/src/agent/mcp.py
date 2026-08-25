"""Codex 风格的 MCP Host：加载 Server、发现工具并接入现有 ToolRegistry。"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import ctypes
from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import tomllib
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx2
from mcp.client import Client
from mcp.client.auth import OAuthClientProvider
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

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
_MAX_RESOURCE_RESULT_BYTES = 64 * 1_024


class McpApproval(str, Enum):
    AUTO = "auto"
    PROMPT = "prompt"
    WRITES = "writes"
    APPROVE = "approve"


@dataclass(frozen=True, slots=True)
class McpOAuthConfig:
    client_id: str | None = None
    callback_port: int | None = None


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
    auth: str = "oauth"
    scopes: tuple[str, ...] = ()
    oauth: McpOAuthConfig | None = None
    oauth_resource: str | None = None
    enabled: bool = True
    required: bool = False
    supports_parallel_tool_calls: bool = False
    startup_timeout_sec: float = 10.0
    tool_timeout_sec: float = 60.0
    default_tools_approval_mode: McpApproval = McpApproval.AUTO
    enabled_tools: frozenset[str] | None = None
    disabled_tools: frozenset[str] = frozenset()
    tool_approval_modes: dict[str, McpApproval] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class McpConfig:
    servers: tuple[McpServerConfig, ...] = ()
    oauth_callback_port: int | None = None
    oauth_callback_url: str | None = None


@dataclass(slots=True)
class _OAuthFlow:
    redirect: asyncio.Future[str]
    callback: asyncio.Future[AuthorizationCodeResult]
    state: str | None = None


class _OAuthStorage:
    def __init__(
        self, path: Path, server: str, configured_client_id: str | None = None
    ) -> None:
        self._path = path
        self._server = server
        self._configured_client_id = configured_client_id

    async def get_tokens(self) -> OAuthToken | None:
        value = self._entry().get("tokens")
        return OAuthToken.model_validate(value) if isinstance(value, dict) else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._update("tokens", tokens.model_dump(mode="json", exclude_none=True))

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        value = self._entry().get("client_info")
        stored = (
            OAuthClientInformationFull.model_validate(value)
            if isinstance(value, dict)
            else None
        )
        if self._configured_client_id and (
            stored is None or stored.client_id != self._configured_client_id
        ):
            return OAuthClientInformationFull(client_id=self._configured_client_id)
        return stored

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._update(
            "client_info", client_info.model_dump(mode="json", exclude_none=True)
        )

    def has_tokens(self) -> bool:
        return isinstance(self._entry().get("tokens"), dict)

    def clear(self) -> None:
        data = self._read()
        if self._server in data:
            del data[self._server]
            self._write(data)

    def _entry(self) -> dict[str, object]:
        value = self._read().get(self._server, {})
        return value if isinstance(value, dict) else {}

    def _update(self, key: str, value: object) -> None:
        data = self._read()
        entry = data.setdefault(self._server, {})
        if not isinstance(entry, dict):
            entry = {}
            data[self._server] = entry
        entry[key] = value
        self._write(data)

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            raw = self._path.read_bytes()
            if raw.startswith(b"DPAPI\0"):
                raw = _dpapi(raw[6:], protect=False)
            value = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"无法读取 MCP OAuth 凭据: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("MCP OAuth 凭据格式无效")
        return value

    def _write(self, value: dict[str, Any]) -> None:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        if os.name == "nt":
            raw = b"DPAPI\0" + _dpapi(raw, protect=True)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb", dir=self._path.parent, delete=False
            ) as output:
                output.write(raw)
                temporary = Path(output.name)
            if os.name != "nt":
                temporary.chmod(0o600)
            os.replace(temporary, self._path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


@dataclass(slots=True)
class _McpConnection:
    config: McpServerConfig
    client: Client
    close_event: asyncio.Event
    worker: asyncio.Task[None]

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

    async def request(self, operation: str, *args: object, **kwargs: object) -> Any:
        try:
            async with asyncio.timeout(self.config.tool_timeout_sec):
                return await getattr(self.client, operation)(*args, **kwargs)
        except TimeoutError as exc:
            raise RuntimeError(
                f"MCP Server {self.config.name} 的 {operation} 请求超时"
            ) from exc

    async def close(self) -> None:
        self.close_event.set()
        await self.worker


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


class _McpResourceHandler:
    required_access = ToolAccess.READ_ONLY
    supports_parallel_tool_calls = True

    def __init__(self, manager: McpManager) -> None:
        self._manager = manager


class ListMcpResourcesHandler(_McpResourceHandler):
    spec = ToolSpec(
        name="list_mcp_resources",
        description=(
            "Lists resources provided by MCP servers. Resources allow servers to share "
            "data that provides context to language models, such as files, database "
            "schemas, or application-specific information."
        ),
        parameters={
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "MCP server name."},
                "cursor": {"type": "string", "description": "Opaque pagination cursor."},
            },
            "additionalProperties": False,
        },
    )

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> str:
        del granted_access, mode
        return await self._manager.list_resources(arguments)


class ListMcpResourceTemplatesHandler(_McpResourceHandler):
    spec = ToolSpec(
        name="list_mcp_resource_templates",
        description=(
            "Lists resource templates provided by MCP servers. Resource templates expose "
            "parameterized application data and context."
        ),
        parameters={
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "MCP server name."},
                "cursor": {"type": "string", "description": "Opaque pagination cursor."},
            },
            "additionalProperties": False,
        },
    )

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> str:
        del granted_access, mode
        return await self._manager.list_resource_templates(arguments)


class ReadMcpResourceHandler(_McpResourceHandler):
    spec = ToolSpec(
        name="read_mcp_resource",
        description="Reads a specific resource from an MCP server.",
        parameters={
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "Configured MCP server name."},
                "uri": {"type": "string", "description": "Resource URI returned by the server."},
            },
            "required": ["server", "uri"],
            "additionalProperties": False,
        },
    )

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> str:
        del granted_access, mode
        return await self._manager.read_resource(arguments)


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
        self._settings = McpConfig()
        self._configs: tuple[McpServerConfig, ...] = ()
        self._connections: dict[str, _McpConnection] = {}
        self._handlers: tuple[Any, ...] = ()
        self._failures: dict[str, str] = {}
        self._oauth_flows: dict[str, _OAuthFlow] = {}
        self._oauth_tasks: dict[str, asyncio.Task[None]] = {}
        self._instructions: str | None = None
        self._started = False

    @property
    def instructions(self) -> str | None:
        return self._instructions

    def handlers(self) -> tuple[Any, ...]:
        return self._handlers

    async def start(self) -> None:
        if self._started:
            return
        self._settings = load_mcp_settings(self._config_path)
        configs = self._settings.servers
        self._configs = configs
        self._failures.clear()
        enabled = tuple(config for config in configs if config.enabled)
        # 逐个启动限制第三方进程和网络连接的瞬时压力；连接本身各有常驻 worker。
        results: list[object] = []
        for config in enabled:
            try:
                results.append(await self._open(config))
            except asyncio.CancelledError:
                await asyncio.gather(
                    *(
                        result[0].close()
                        for result in reversed(results)
                        if isinstance(result, tuple)
                    ),
                    return_exceptions=True,
                )
                raise
            except Exception as exc:
                results.append(exc)
        required_failures: list[str] = []
        handlers: list[McpToolHandler] = []
        instructions: list[str] = []
        catalog_bytes = 0
        for config, result in zip(enabled, results, strict=True):
            if isinstance(result, BaseException):
                self._failures[config.name] = str(result)
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
        resource_handlers: list[Any] = []
        if any(_supports(connection.client, "resources") for connection in self._connections.values()):
            resource_handlers = [
                ListMcpResourcesHandler(self),
                ListMcpResourceTemplatesHandler(self),
                ReadMcpResourceHandler(self),
            ]
        self._handlers = tuple([*handlers, *resource_handlers])
        joined = "\n\n".join(instructions)[:_MAX_INSTRUCTIONS]
        self._instructions = (
            f"# MCP Server Instructions\n\n{joined}" if joined else None
        )
        self._started = True

    async def close(self) -> None:
        oauth_tasks = tuple(self._oauth_tasks.values())
        self._oauth_tasks.clear()
        self._oauth_flows.clear()
        for task in oauth_tasks:
            task.cancel()
        if oauth_tasks:
            await asyncio.gather(*oauth_tasks, return_exceptions=True)
        connections = tuple(reversed(tuple(self._connections.values())))
        self._connections.clear()
        self._handlers = ()
        self._failures.clear()
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

    async def begin_oauth_login(
        self, server: str, fallback_callback_url: str
    ) -> dict[str, object]:
        settings = load_mcp_settings(self._config_path)
        config = next((item for item in settings.servers if item.name == server), None)
        if config is None:
            raise ValueError(f"未配置 MCP Server: {server}")
        if config.url is None:
            raise ValueError("stdio MCP Server 不支持 OAuth")
        if server in self._oauth_tasks:
            raise RuntimeError(f"MCP Server {server} 已在等待 OAuth 授权")
        callback_url = _oauth_callback_url(config, settings, fallback_callback_url)
        loop = asyncio.get_running_loop()
        flow = _OAuthFlow(loop.create_future(), loop.create_future())
        task = asyncio.create_task(
            self._run_oauth_login(config, callback_url, flow),
            name=f"mcp-oauth-{server}",
        )
        self._oauth_flows[server] = flow
        self._oauth_tasks[server] = task
        done, _ = await asyncio.wait(
            {flow.redirect, task}, timeout=30, return_when=asyncio.FIRST_COMPLETED
        )
        if flow.redirect in done:
            return {"server": server, "authorization_url": flow.redirect.result()}
        if task in done:
            try:
                task.result()
            finally:
                self._oauth_flows.pop(server, None)
                self._oauth_tasks.pop(server, None)
            return {"server": server, "authenticated": True}
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._oauth_flows.pop(server, None)
        self._oauth_tasks.pop(server, None)
        raise RuntimeError(f"MCP Server {server} 未在 30 秒内返回 OAuth 授权地址")

    async def complete_oauth_login(
        self,
        *,
        code: str,
        state: str | None,
        iss: str | None,
        error: str | None = None,
    ) -> str:
        match = next(
            (
                (name, flow)
                for name, flow in self._oauth_flows.items()
                if flow.state is not None and flow.state == state
            ),
            None,
        )
        if match is None:
            raise ValueError("OAuth 回调已失效或 state 不匹配")
        server, flow = match
        if flow.callback.done():
            raise ValueError("OAuth 回调已处理")
        if error:
            flow.callback.set_exception(RuntimeError(f"OAuth 授权失败: {error}"))
        else:
            flow.callback.set_result(
                AuthorizationCodeResult(code=code, state=state, iss=iss)
            )
        task = self._oauth_tasks[server]
        try:
            async with asyncio.timeout(60):
                await task
        finally:
            self._oauth_flows.pop(server, None)
            self._oauth_tasks.pop(server, None)
        return server

    async def logout_oauth(self, server: str) -> None:
        config = next(
            (item for item in load_mcp_config(self._config_path) if item.name == server),
            None,
        )
        if config is None:
            raise ValueError(f"未配置 MCP Server: {server}")
        self._oauth_storage(config).clear()

    async def list_resources(self, arguments: dict[str, object]) -> str:
        return await self._list_resources(arguments, templates=False)

    async def list_resource_templates(self, arguments: dict[str, object]) -> str:
        return await self._list_resources(arguments, templates=True)

    async def read_resource(self, arguments: dict[str, object]) -> str:
        server = _required_argument(arguments, "server")
        uri = _required_argument(arguments, "uri")
        connection = self._resource_connection(server)
        result = await connection.request("read_resource", uri)
        return _json_result(
            {"server": server, "uri": uri, **_model_payload(result)}
        )

    def status(self) -> dict[str, object]:
        configs = self._configs or load_mcp_config(self._config_path)
        servers: list[dict[str, object]] = []
        for config in configs:
            connection = self._connections.get(config.name)
            server_info = getattr(connection.client, "server_info", None) if connection else None
            info = _model_payload(server_info) if server_info is not None else None
            tools = [
                handler.spec.name
                for handler in self._handlers
                if isinstance(handler, McpToolHandler)
                and handler._connection.config.name == config.name
            ]
            servers.append(
                {
                    "name": config.name,
                    "transport": "stdio" if config.command is not None else "streamable_http",
                    "enabled": config.enabled,
                    "required": config.required,
                    "status": (
                        "disabled"
                        if not config.enabled
                        else "ready"
                        if connection is not None
                        else "failed"
                        if config.name in self._failures
                        else "not_started"
                    ),
                    "error": self._failures.get(config.name),
                    "server_info": info,
                    "tools": tools,
                    "supports_resources": bool(
                        connection is not None
                        and _supports(connection.client, "resources")
                    ),
                    "auth": self._auth_status(config, connection),
                }
            )
        return {"servers": servers}

    def _auth_status(
        self, config: McpServerConfig, connection: _McpConnection | None
    ) -> str:
        if config.command is not None:
            return "unsupported"
        if _has_authorization(_http_headers(config)):
            return "bearer"
        if self._oauth_storage(config).has_tokens():
            return "oauth_authenticated"
        if config.name in self._oauth_tasks:
            return "oauth_pending"
        if config.oauth or config.scopes or config.oauth_resource:
            return "oauth_not_authenticated"
        if connection is not None:
            return "not_required"
        failure = self._failures.get(config.name, "").lower()
        if any(marker in failure for marker in ("oauth", "redirect handler", "401", "unauthorized")):
            return "oauth_not_authenticated"
        return "unknown"

    def _oauth_storage(self, config: McpServerConfig) -> _OAuthStorage:
        client_id = config.oauth.client_id if config.oauth else None
        return _OAuthStorage(_oauth_storage_path(self._config_path), config.name, client_id)

    async def _run_oauth_login(
        self, config: McpServerConfig, callback_url: str, flow: _OAuthFlow
    ) -> None:
        provider = _oauth_provider(
            config,
            self._oauth_storage(config),
            callback_url=callback_url,
            flow=flow,
        )
        async with httpx2.AsyncClient(
            auth=provider,
            headers=_http_headers(config),
            follow_redirects=True,
            timeout=httpx2.Timeout(30.0, read=300.0),
        ) as http_client:
            async with Client(
                streamable_http_client(config.url or "", http_client=http_client),
                mode="auto",
                read_timeout_seconds=config.tool_timeout_sec,
            ):
                pass

    async def _list_resources(
        self, arguments: dict[str, object], *, templates: bool
    ) -> str:
        unknown = set(arguments) - {"server", "cursor"}
        if unknown:
            raise ValueError(f"包含未知参数: {', '.join(sorted(unknown))}")
        server = arguments.get("server")
        cursor = arguments.get("cursor")
        if server is not None and (not isinstance(server, str) or not server.strip()):
            raise ValueError("server 必须是非空字符串")
        if cursor is not None and (not isinstance(cursor, str) or not cursor):
            raise ValueError("cursor 必须是非空字符串")
        operation = "list_resource_templates" if templates else "list_resources"
        list_key = "resourceTemplates" if templates else "resources"
        if isinstance(server, str):
            connection = self._resource_connection(server.strip())
            result = await connection.request(operation, cursor=cursor)
            return _json_result(
                {"server": server.strip(), **_model_payload(result)}
            )
        if cursor is not None:
            raise ValueError("跨 Server 列出资源时不能使用 cursor，请先指定 server")
        connections = [
            connection
            for connection in self._connections.values()
            if _supports(connection.client, "resources")
        ]
        results = await asyncio.gather(
            *(connection.request(operation) for connection in connections),
            return_exceptions=True,
        )
        items: list[object] = []
        errors: list[dict[str, str]] = []
        for connection, result in zip(connections, results, strict=True):
            if isinstance(result, BaseException):
                errors.append({"server": connection.config.name, "error": str(result)})
                continue
            payload = _model_payload(result)
            for item in payload.get(list_key, []):
                if isinstance(item, dict):
                    items.append({"server": connection.config.name, **item})
        return _json_result({list_key: items, "errors": errors})

    def _resource_connection(self, server: str) -> _McpConnection:
        connection = self._connections.get(server)
        if connection is None:
            raise ValueError(f"MCP Server 未连接: {server}")
        if not _supports(connection.client, "resources"):
            raise ValueError(f"MCP Server 不支持 Resources: {server}")
        return connection

    async def _open(
        self, config: McpServerConfig
    ) -> tuple[_McpConnection, tuple[Any, ...]]:
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[tuple[_McpConnection, tuple[Any, ...]]] = (
            loop.create_future()
        )
        close_event = asyncio.Event()

        async def worker() -> None:
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
                    auth = None
                    if not _has_authorization(headers):
                        auth = _oauth_provider(config, self._oauth_storage(config))
                    http_client = await resources.enter_async_context(
                        httpx2.AsyncClient(
                            headers=headers,
                            auth=auth,
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
                tools = (
                    await _list_tools(client, config)
                    if _supports(client, "tools", fallback=True)
                    else ()
                )
                task = asyncio.current_task()
                assert task is not None
                if ready.done():
                    return
                ready.set_result(
                    (_McpConnection(config, client, close_event, task), tools)
                )
                await close_event.wait()
            except BaseException as exc:
                if not ready.done():
                    ready.set_exception(exc)
            finally:
                await resources.aclose()

        worker_task = asyncio.create_task(worker(), name=f"mcp-{config.name}")
        try:
            async with asyncio.timeout(config.startup_timeout_sec):
                return await asyncio.shield(ready)
        except TimeoutError as exc:
            ready.cancel()
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
            raise RuntimeError(
                f"MCP Server {config.name} 启动超过 {config.startup_timeout_sec:g} 秒"
            ) from exc
        except BaseException:
            ready.cancel()
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
            raise


def load_mcp_config(path: Path | None) -> tuple[McpServerConfig, ...]:
    return load_mcp_settings(path).servers


def load_mcp_settings(path: Path | None) -> McpConfig:
    return _parse_mcp_settings(read_mcp_config(path))


def read_mcp_config(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    if not path.is_file():
        raise ValueError(f"MCP 配置不是文件: {path}")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"无法读取 MCP 配置 {path}: {exc}") from exc
    if len(content) > _MAX_CONFIG_BYTES:
        raise ValueError("MCP 配置不能超过 1 MiB")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"MCP 配置必须使用 UTF-8: {path}") from exc


def save_mcp_config(path: Path | None, content: str) -> None:
    if path is None:
        raise ValueError("未配置 MCP 配置文件路径")
    _parse_mcp_settings(content)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, delete=False, newline="\n"
            ) as temporary:
                temporary.write(content)
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    except OSError as exc:
        raise ValueError(f"无法保存 MCP 配置 {path}: {exc}") from exc


def _parse_mcp_config(content: str) -> tuple[McpServerConfig, ...]:
    return _parse_mcp_settings(content).servers


def _parse_mcp_settings(content: str) -> McpConfig:
    try:
        encoded_size = len(content.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError("MCP 配置必须是有效的 UTF-8 文本") from exc
    if encoded_size > _MAX_CONFIG_BYTES:
        raise ValueError("MCP 配置不能超过 1 MiB")
    try:
        root = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"MCP 配置 TOML 无效: {exc}") from exc
    allowed_root = {
        "mcp_servers",
        "mcp_oauth_callback_port",
        "mcp_oauth_callback_url",
    }
    if unknown := set(root) - allowed_root:
        raise ValueError(
            "MCP 配置顶层包含未知字段: " + ", ".join(sorted(unknown))
        )
    raw_servers = root.get("mcp_servers", {})
    if not isinstance(raw_servers, dict):
        raise ValueError("mcp_servers 必须是 TOML Table")
    if len(raw_servers) > _MAX_SERVERS:
        raise ValueError(f"MCP Server 不能超过 {_MAX_SERVERS} 个")
    callback_port = root.get("mcp_oauth_callback_port")
    if callback_port is not None:
        callback_port = _port(callback_port, "mcp_oauth_callback_port", root=True)
    callback_url = root.get("mcp_oauth_callback_url")
    if callback_url is not None:
        callback_url = _absolute_http_url(
            callback_url, "mcp_oauth_callback_url", root=True
        )
    return McpConfig(
        tuple(_server_config(name, value) for name, value in raw_servers.items()),
        callback_port,
        callback_url,
    )


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
        "auth",
        "scopes",
        "oauth",
        "oauth_resource",
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
        url = _absolute_http_url(url, f"{name}.url")

    if command is not None:
        for field_name in (
            "bearer_token_env_var",
            "http_headers",
            "env_http_headers",
            "auth",
            "oauth",
            "oauth_resource",
            "scopes",
        ):
            if field_name in value:
                raise ValueError(
                    f"mcp_servers.{name}.{field_name} 不支持 stdio 传输"
                )
    else:
        for field_name in ("args", "env", "env_vars", "cwd"):
            if field_name in value:
                raise ValueError(
                    f"mcp_servers.{name}.{field_name} 不支持 streamable_http 传输"
                )

    auth = value.get("auth", "oauth")
    if auth != "oauth":
        raise ValueError(f"mcp_servers.{name}.auth 当前只支持 oauth")
    oauth_value = value.get("oauth")
    oauth: McpOAuthConfig | None = None
    if oauth_value is not None:
        if not isinstance(oauth_value, dict) or set(oauth_value) - {
            "client_id",
            "callback_port",
        }:
            raise ValueError(
                f"mcp_servers.{name}.oauth 只能包含 client_id 和 callback_port"
            )
        client_id = _optional_text(oauth_value.get("client_id"), f"{name}.oauth.client_id")
        callback_port = oauth_value.get("callback_port")
        oauth = McpOAuthConfig(
            client_id,
            _port(callback_port, f"{name}.oauth.callback_port")
            if callback_port is not None
            else None,
        )
    oauth_resource = _optional_text(
        value.get("oauth_resource"), f"{name}.oauth_resource"
    )
    if oauth_resource is not None and not urlparse(oauth_resource).scheme:
        raise ValueError(f"mcp_servers.{name}.oauth_resource 必须是绝对 URI")

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
        auth=auth,
        scopes=_string_tuple(value.get("scopes", ()), f"{name}.scopes"),
        oauth=oauth,
        oauth_resource=oauth_resource,
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
        for header in tuple(headers):
            if header.lower() == "authorization":
                del headers[header]
        headers["Authorization"] = (
            f"Bearer {os.environ[config.bearer_token_env_var]}"
        )
    return headers


def _has_authorization(headers: dict[str, str]) -> bool:
    return any(name.lower() == "authorization" and value for name, value in headers.items())


def _supports(client: object, capability: str, *, fallback: bool = False) -> bool:
    try:
        capabilities = getattr(client, "server_capabilities")
    except (AttributeError, RuntimeError):
        return fallback
    return getattr(capabilities, capability, None) is not None


def _required_argument(arguments: dict[str, object], name: str) -> str:
    if set(arguments) - {"server", "uri"}:
        raise ValueError("read_mcp_resource 只能包含 server 和 uri")
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > 8_192:
        raise ValueError(f"{name} 必须是非空字符串")
    return value.strip()


def _model_payload(value: object) -> dict[str, Any]:
    dump = getattr(value, "model_dump", None)
    if not callable(dump):
        raise RuntimeError("MCP Server 返回了无效响应")
    payload = dump(mode="json", by_alias=True, exclude_none=True)
    if not isinstance(payload, dict):
        raise RuntimeError("MCP Server 返回了无效响应")
    return payload


def _json_result(value: object) -> str:
    result = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(result.encode("utf-8")) > _MAX_RESOURCE_RESULT_BYTES:
        raise RuntimeError("MCP Resource 结果超过 64 KiB，请指定 Server 或缩小查询范围")
    return result


def _oauth_storage_path(config_path: Path | None) -> Path:
    if config_path is None:
        return Path.home() / ".obsidian-personal-agent" / "mcp-oauth.json"
    return config_path.with_suffix(config_path.suffix + ".oauth")


def _oauth_callback_url(
    config: McpServerConfig, settings: McpConfig, fallback: str
) -> str:
    if settings.oauth_callback_url:
        return settings.oauth_callback_url
    desired_port = (
        config.oauth.callback_port if config.oauth and config.oauth.callback_port else None
    ) or settings.oauth_callback_port
    parsed = urlparse(fallback)
    actual_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if desired_port is not None and desired_port != actual_port:
        raise ValueError(
            f"OAuth callback_port={desired_port} 与当前 Agent 端口 {actual_port} 不同；"
            "请让 Agent 使用该端口，或设置 mcp_oauth_callback_url"
        )
    return fallback


def _oauth_provider(
    config: McpServerConfig,
    storage: _OAuthStorage,
    *,
    callback_url: str | None = None,
    flow: _OAuthFlow | None = None,
) -> OAuthClientProvider:
    async def redirect_handler(url: str) -> None:
        assert flow is not None
        flow.state = parse_qs(urlparse(url).query).get("state", [None])[0]
        if not flow.redirect.done():
            flow.redirect.set_result(url)

    async def callback_handler() -> AuthorizationCodeResult:
        assert flow is not None
        return await flow.callback

    metadata = OAuthClientMetadata(
        redirect_uris=[callback_url] if callback_url else ["http://127.0.0.1/"],
        client_name="Obsidian Personal Agent",
        scope=" ".join(config.scopes) or None,
    )
    provider = OAuthClientProvider(
        config.url or "",
        metadata,
        storage,
        redirect_handler=redirect_handler if flow else None,
        callback_handler=callback_handler if flow else None,
    )
    if config.oauth_resource:
        resource = config.oauth_resource
        setattr(provider.context, "get_resource_url", lambda: resource)
        setattr(provider.context, "should_include_resource_param", lambda *_args: True)
    return provider


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise ValueError("DPAPI 仅在 Windows 可用")
    source = ctypes.create_string_buffer(data)
    source_blob = _DataBlob(
        len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte))
    )
    output_blob = _DataBlob()
    function = (
        ctypes.windll.crypt32.CryptProtectData
        if protect
        else ctypes.windll.crypt32.CryptUnprotectData
    )
    arguments = (
        (ctypes.byref(source_blob), "MCP OAuth", None, None, None, 1, ctypes.byref(output_blob))
        if protect
        else (ctypes.byref(source_blob), None, None, None, None, 1, ctypes.byref(output_blob))
    )
    if not function(*arguments):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)


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


def _absolute_http_url(value: object, field_name: str, *, root: bool = False) -> str:
    prefix = "" if root else "mcp_servers."
    if not isinstance(value, str) or not value.strip() or len(value) > 8_192:
        raise ValueError(f"{prefix}{field_name} 必须是 HTTP(S) 地址")
    result = value.strip()
    parsed = urlparse(result)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{prefix}{field_name} 必须是 HTTP(S) 地址")
    if parsed.username or parsed.password:
        raise ValueError(f"{prefix}{field_name} 不能包含用户名或密码")
    return result


def _port(value: object, field_name: str, *, root: bool = False) -> int:
    prefix = "" if root else "mcp_servers."
    if type(value) is not int or not 1 <= value <= 65_535:
        raise ValueError(f"{prefix}{field_name} 必须是 1 到 65535 的整数")
    return value


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
