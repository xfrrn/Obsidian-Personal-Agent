"""MCP Host 的最小离线验证。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent.mcp import (
    McpServerConfig,
    McpManager,
    _OAuthStorage,
    _oauth_provider,
    load_mcp_config,
    load_mcp_settings,
    save_mcp_config,
)
from mcp.shared.auth import OAuthToken
from mcp.client.auth import OAuthClientProvider
from agent.permissions import (
    ApprovalPolicy,
    PermissionDecisionKind,
    PermissionPolicy,
    PermissionRequirement,
    ToolAccess,
)
from agent.protocol.mode import ModeKind


class _FakeResult:
    def model_dump(self, **_kwargs: object) -> dict[str, object]:
        return {
            "content": [{"type": "text", "text": "完成"}],
            "structuredContent": {"id": "job-1"},
            "isError": False,
        }


class _FakeClient:
    instances: list[_FakeClient] = []

    def __init__(self, _target: object, **_kwargs: object) -> None:
        self.instructions = "先读取状态，再执行写操作。"
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False
        self.instances.append(self)

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.closed = True

    async def list_tools(self, **_kwargs: object) -> object:
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="read",
                    description="读取",
                    input_schema={"type": "object"},
                    annotations=SimpleNamespace(read_only_hint=True),
                ),
                SimpleNamespace(
                    name="write",
                    description="写入",
                    input_schema={"type": "object"},
                    annotations=SimpleNamespace(read_only_hint=False),
                ),
                SimpleNamespace(
                    name="hidden",
                    description="隐藏",
                    input_schema={"type": "object"},
                    annotations=None,
                ),
            ],
            next_cursor=None,
        )

    async def call_tool(
        self, name: str, arguments: dict[str, object], **_kwargs: object
    ) -> _FakeResult:
        self.calls.append((name, arguments))
        return _FakeResult()


class McpHostTest(unittest.IsolatedAsyncioTestCase):
    async def test_http_transport_resolves_headers_from_environment(self) -> None:
    async def test_persisted_oauth_token_is_refreshed_after_restart(self) -> None:
        requested: list[str] = []
        metadata = SimpleNamespace(token_endpoint="https://auth.example/token")

        class FakeHttpClient:
            async def __aenter__(self) -> FakeHttpClient:
                return self

            async def __aexit__(self, *_args: object) -> None:
                pass

            async def get(self, url: str) -> object:
                requested.append(url)
                return object()

        async def load_stored(provider: OAuthClientProvider) -> None:
            provider.context.current_tokens = OAuthToken(
                access_token="expired", refresh_token="refresh"
            )
            provider.context.client_info = SimpleNamespace(
                client_id="client", issuer="https://auth.example"
            )
            provider._initialized = True

        async def discover(_response: object) -> tuple[bool, object]:
            return True, metadata

        provider = _oauth_provider(
            McpServerConfig(name="remote", url="https://resource.example/mcp"),
            SimpleNamespace(),
        )
        with (
            patch.object(OAuthClientProvider, "_initialize", load_stored),
            patch("agent.mcp.httpx2.AsyncClient", return_value=FakeHttpClient()),
            patch("agent.mcp.handle_auth_metadata_response", discover),
        ):
            await provider._initialize()

        self.assertIs(provider.context.oauth_metadata, metadata)
        self.assertFalse(provider.context.is_token_valid())
        self.assertTrue(requested[0].startswith("https://auth.example/"))

        class FakeHttpClient:
            created: FakeHttpClient | None = None

            def __init__(self, **kwargs: object) -> None:
                self.headers = kwargs["headers"]
                self.closed = False
                FakeHttpClient.created = self

            async def __aenter__(self) -> FakeHttpClient:
                return self

            async def __aexit__(self, *_args: object) -> None:
                self.closed = True

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(
                """
[mcp_servers.remote]
url = "https://example.com/mcp"
bearer_token_env_var = "MCP_TEST_TOKEN"
http_headers = { X-Static = "fixed" }
env_http_headers = { X-Dynamic = "MCP_TEST_HEADER" }
""",
                encoding="utf-8",
            )
            with (
                patch.dict(
                    os.environ,
                    {"MCP_TEST_TOKEN": "secret", "MCP_TEST_HEADER": "dynamic"},
                ),
                patch("agent.mcp.httpx2.AsyncClient", FakeHttpClient),
                patch("agent.mcp.streamable_http_client", return_value=object()),
                patch("agent.mcp.Client", _FakeClient),
            ):
                manager = McpManager(config)
                await manager.start()
                await manager.close()

            self.assertEqual(
                FakeHttpClient.created.headers if FakeHttpClient.created else None,
                {
                    "X-Static": "fixed",
                    "X-Dynamic": "dynamic",
                    "Authorization": "Bearer secret",
                },
            )
            self.assertTrue(FakeHttpClient.created and FakeHttpClient.created.closed)

    async def test_real_stdio_server_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = root / "server.py"
            server.write_text(
                """
from mcp.server import MCPServer
from mcp_types import ToolAnnotations

server = MCPServer("fixture", instructions="Use echo for text.")

@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def echo(text: str) -> str:
    return "echo:" + text

@server.resource("note://fixture", name="fixture-note")
def note() -> str:
    return "resource text"

server.run()
""",
                encoding="utf-8",
            )
            config = root / "config.toml"
            config.write_text(
                "[mcp_servers.fixture]\n"
                f"command = {json.dumps(sys.executable)}\n"
                f"args = [{json.dumps(str(server))}]\n",
                encoding="utf-8",
            )
            manager = McpManager(config)
            await asyncio.create_task(manager.start())
            try:
                self.assertEqual(
                    [handler.spec.name for handler in manager.handlers()],
                    [
                        "mcp__fixture__echo",
                        "list_mcp_resources",
                        "list_mcp_resource_templates",
                        "read_mcp_resource",
                    ],
                )
                execution = await manager.handlers()[0].run({"text": "hello"})
                self.assertIn("echo:hello", execution.content)
                self.assertIn("Use echo for text", manager.instructions or "")
                listed = json.loads(await manager.list_resources({"server": "fixture"}))
                self.assertEqual(listed["resources"][0]["uri"], "note://fixture")
                read = json.loads(
                    await manager.read_resource(
                        {"server": "fixture", "uri": "note://fixture"}
                    )
                )
                self.assertEqual(read["contents"][0]["text"], "resource text")
            finally:
                await asyncio.create_task(manager.close())

    async def test_discovers_filters_approves_and_calls_mcp_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(
                """
[mcp_servers.demo]
command = "unused"
enabled_tools = ["read", "write", "hidden"]
disabled_tools = ["hidden"]
default_tools_approval_mode = "writes"

[mcp_servers.demo.tools.write]
approval_mode = "prompt"
""",
                encoding="utf-8",
            )
            _FakeClient.instances.clear()
            with patch("agent.mcp.Client", _FakeClient):
                manager = McpManager(config)
                await manager.start()
                handlers = manager.handlers()

                self.assertEqual(
                    [handler.spec.name for handler in handlers],
                    ["mcp__demo__read", "mcp__demo__write"],
                )
                read_requirement = handlers[0].permission_requirement({})
                write_requirement = handlers[1].permission_requirement({})
                self.assertFalse(read_requirement.approval_required)
                self.assertTrue(write_requirement.approval_required)
                self.assertIs(read_requirement.access, ToolAccess.READ_ONLY)
                self.assertIs(write_requirement.access, ToolAccess.HOST_EXECUTION)
                execution = await handlers[1].run({"url": "https://example.com"})

                self.assertEqual(execution.content, '完成\n{"id":"job-1"}')
                self.assertFalse(execution.is_error)
                self.assertIn("MCP server: demo", manager.instructions or "")
                self.assertEqual(
                    _FakeClient.instances[0].calls,
                    [("write", {"url": "https://example.com"})],
                )
                await manager.close()

            self.assertTrue(_FakeClient.instances[0].closed)

    async def test_required_server_failure_stops_startup(self) -> None:
        class FailingClient(_FakeClient):
            async def __aenter__(self) -> _FakeClient:
                raise RuntimeError("offline")

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(
                '[mcp_servers.demo]\ncommand = "unused"\nrequired = true\n',
                encoding="utf-8",
            )
            with patch("agent.mcp.Client", FailingClient):
                with self.assertRaisesRegex(RuntimeError, "必需的 MCP Server"):
                    await McpManager(config).start()

    def test_config_rejects_mixed_transports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(
                '[mcp_servers.demo]\ncommand = "server"\nurl = "https://example.com/mcp"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "必须且只能设置 command 或 url"):
                load_mcp_config(config)

    def test_save_config_validates_before_replacing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            original = '[mcp_servers.demo]\ncommand = "server"\n'
            config.write_text(original, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "TOML 无效"):
                save_mcp_config(config, "[mcp_servers.demo\n")
            self.assertEqual(config.read_text(encoding="utf-8"), original)

            updated = '[mcp_servers.remote]\nurl = "https://example.com/mcp"\n'
            save_mcp_config(config, updated)
            self.assertEqual(config.read_text(encoding="utf-8"), updated)

    def test_codex_compatible_oauth_fields_are_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(
                """
mcp_oauth_callback_port = 8000
mcp_oauth_callback_url = "http://127.0.0.1:8000/api/mcp/oauth/callback"

[mcp_servers.remote]
url = "https://example.com/mcp"
scopes = ["read", "write"]
oauth_resource = "https://example.com/"

[mcp_servers.remote.oauth]
client_id = "obsidian-agent"
callback_port = 8000
""",
                encoding="utf-8",
            )
            settings = load_mcp_settings(config)

        self.assertEqual(settings.oauth_callback_port, 8000)
        self.assertEqual(settings.servers[0].scopes, ("read", "write"))
        self.assertEqual(settings.servers[0].oauth.client_id, "obsidian-agent")

    def test_transport_specific_fields_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(
                '[mcp_servers.demo]\ncommand = "server"\nhttp_headers = { X = "y" }\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "不支持 stdio"):
                load_mcp_config(config)

    async def test_oauth_login_matches_callback_by_state(self) -> None:
        async def fake_login(
            _manager: McpManager, _config: object, _callback: str, flow: object
        ) -> None:
            flow.state = "expected-state"
            flow.redirect.set_result(
                "https://auth.example/authorize?state=expected-state"
            )
            result = await flow.callback
            self.assertEqual(result.code, "authorization-code")

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(
                '[mcp_servers.remote]\nurl = "https://example.com/mcp"\n',
                encoding="utf-8",
            )
            manager = McpManager(config)
            with patch.object(McpManager, "_run_oauth_login", fake_login):
                started = await manager.begin_oauth_login(
                    "remote", "http://127.0.0.1:8000/api/mcp/oauth/callback"
                )
                completed = await manager.complete_oauth_login(
                    code="authorization-code", state="expected-state", iss=None
                )

        self.assertEqual(completed, "remote")
        self.assertIn("authorization_url", started)

    async def test_oauth_credentials_are_persisted_without_plaintext_on_windows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oauth.json"
            storage = _OAuthStorage(path, "remote")
            await storage.set_tokens(
                OAuthToken(access_token="access-secret", refresh_token="refresh-secret")
            )
            restored = await storage.get_tokens()
            raw = path.read_bytes()

        self.assertEqual(restored.access_token if restored else None, "access-secret")
        if os.name == "nt":
            self.assertTrue(raw.startswith(b"DPAPI\0"))
            self.assertNotIn(b"access-secret", raw)

    def test_permission_override_reuses_existing_policy(self) -> None:
        policy = PermissionPolicy(approval_policy=ApprovalPolicy.ON_REQUEST)
        self.assertIs(
            policy.decide(
                PermissionRequirement(
                    ToolAccess.READ_ONLY, "外部读取", approval_required=True
                )
            ).kind,
            PermissionDecisionKind.REQUIRE_APPROVAL,
        )
        self.assertIs(
            policy.decide(
                PermissionRequirement(
                    ToolAccess.HOST_EXECUTION, approval_required=False
                )
            ).kind,
            PermissionDecisionKind.ALLOW,
        )
        self.assertIs(
            policy.decide(
                PermissionRequirement(
                    ToolAccess.HOST_EXECUTION, approval_required=False
                ),
                ModeKind.PLAN,
            ).kind,
            PermissionDecisionKind.DENY,
        )
