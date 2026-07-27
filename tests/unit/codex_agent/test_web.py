"""Web 宿主的最小自检：新对话必须舍弃前一个会话历史。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event as ThreadEvent, Thread
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent.config.settings import Settings
from agent.llm.types import AssistantResponse, ToolCall
from agent.permissions import SandboxMode
from agent.protocol.event import EventKind
from agent.web.server import AgentHTTPServer, AgentRuntime, _is_loopback_client


AGENT_ROOT = Path(__file__).resolve().parents[3] / "apps" / "local-agent" / "src" / "agent"


class RecordingClient:
    """离线模型替身，记录每个会话真正收到的提示词快照。"""

    def __init__(self) -> None:
        self.requests: list[list[dict[str, object]]] = []

    async def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> AssistantResponse:
        self.requests.append(messages)
        user_text = next(message["content"] for message in reversed(messages) if message["role"] == "user")
        return AssistantResponse(f"收到：{user_text}")


class StreamingClient:
    async def stream_complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]], on_delta: object) -> AssistantResponse:
        await on_delta("逐")
        await on_delta("字")
        return AssistantResponse("逐字")


class SteeringClient:
    """首个请求持续运行，验证第二条输入可直接中断并重启 turn。"""

    def __init__(self) -> None:
        self.started = ThreadEvent()
        self.was_cancelled = False
        self.requests: list[list[dict[str, object]]] = []

    async def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> AssistantResponse:
        self.requests.append(messages)
        if len(self.requests) == 1:
            self.started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.was_cancelled = True
                raise
        return AssistantResponse("已按新输入继续")


class ConcurrentClient:
    """等待共享闸门，证明两个 Session 的模型请求可以同时在途。"""

    def __init__(self, started: ThreadEvent, release: ThreadEvent) -> None:
        self.started = started
        self.release = release

    async def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]]) -> AssistantResponse:
        self.started.set()
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        return AssistantResponse("完成")


class EscalatingWebClient:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: list[dict[str, object]], tools: list[dict[str, object]]
    ) -> AssistantResponse:
        self.calls += 1
        if self.calls == 1:
            return AssistantResponse(
                None,
                (
                    ToolCall(
                        "web-call-1",
                        "exec_command",
                        {
                            "command": "echo web-approved",
                            "sandbox_permissions": "require_escalated",
                            "justification": "HTTP 审批回路测试",
                        },
                    ),
                ),
            )
        return AssistantResponse("命令已完成")


class WebRuntimeTest(unittest.TestCase):
    def test_unbuilt_frontend_fallback_has_no_second_agent_ui(self) -> None:
        page = (AGENT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("npm run build:agent-ui", page)
        self.assertNotIn("/api/messages", page)

    def test_frontend_keeps_session_navigation_enabled_while_a_turn_runs(self) -> None:
        app = (AGENT_ROOT / "web" / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
        self.assertNotIn("disabled={busy || creatingConversation}", app)
        self.assertIn("conversationViews.current[sessionId]", app)
        self.assertIn("const nextMessages = update(view.messages)", app)
        self.assertIn("const nextTraces = update(view.traces)", app)
        self.assertIn("Boolean(runningTurns[activeConversationId])", app)

    def test_frontend_exposes_archive_and_inline_approval_flows(self) -> None:
        app = (AGENT_ROOT / "web" / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")

        self.assertIn("/archive`, {})", app)
        self.assertIn("pendingApprovals", app)
        self.assertIn("仅允许本次", app)
        self.assertNotIn("window.confirm(\n            `允许这一次命令", app)

    def test_frontend_exposes_runtime_permission_switch(self) -> None:
        app = (AGENT_ROOT / "web" / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")

        self.assertIn('aria-label="沙盒权限"', app)
        self.assertIn('requestJson<RuntimePermissions>("/api/permissions"', app)
        self.assertIn('value="danger-full-access"', app)

    def test_frontend_is_a_sidebar_chat_without_metrics_dashboard(self) -> None:
        app = (AGENT_ROOT / "web" / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")

        self.assertIn("session-drawer", app)
        self.assertIn("subscribeToObsidian", app)
        self.assertIn("connection-banner", app)
        self.assertNotIn('requestJson<Metrics>("/api/metrics"', app)
        self.assertNotIn("function Monitor", app)

    def test_new_conversation_uses_a_fresh_agent_session(self) -> None:
        clients: list[RecordingClient] = []

        def client_factory() -> RecordingClient:
            client = RecordingClient()
            clients.append(client)
            return client

        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                api_key=None,
                model="test-model",
                base_url="http://unused",
                system_prompt="test",
                workspace=Path(directory),
                shell_enabled=False,
                request_timeout_seconds=1,
                max_tool_rounds=1,
                session_db_path=Path(directory) / "sessions.db",
            )
            runtime = AgentRuntime(settings, client_factory)
            try:
                runtime.new_conversation()
                first_events = runtime.ask("第一轮")
                runtime.new_conversation()
                second_events = runtime.ask("第二轮")
            finally:
                runtime.close()

        self.assertEqual([event.kind for event in first_events], [
            EventKind.TURN_STARTED,
            EventKind.ASSISTANT_MESSAGE,
            EventKind.TURN_FINISHED,
        ])
        self.assertEqual(second_events[1].text, "收到：第二轮")
        self.assertEqual(len(clients), 2)
        self.assertNotIn("第一轮", str(clients[1].requests))

    def test_sessions_survive_runtime_restart_and_stay_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = _settings(Path(directory))
            first_runtime = AgentRuntime(settings, RecordingClient)
            try:
                first_id = first_runtime.new_conversation()
                second_id = first_runtime.new_conversation()
                first_runtime.ask("第一会话", first_id)
                first_runtime.ask("第二会话", second_id)
            finally:
                first_runtime.close()

            resumed_clients: list[RecordingClient] = []

            def client_factory() -> RecordingClient:
                client = RecordingClient()
                resumed_clients.append(client)
                return client

            second_runtime = AgentRuntime(settings, client_factory)
            try:
                second_runtime.ask("继续第一会话", first_id)
                first_history = second_runtime.conversation(first_id)["messages"]
                second_history = second_runtime.conversation(second_id)["messages"]
            finally:
                second_runtime.close()

        self.assertEqual(
            [message["text"] for message in first_history],
            ["第一会话", "收到：第一会话", "继续第一会话", "收到：继续第一会话"],
        )
        self.assertEqual(
            [message["text"] for message in second_history],
            ["第二会话", "收到：第二会话"],
        )
        self.assertIn("第一会话", str(resumed_clients[0].requests[0]))
        self.assertNotIn("第二会话", str(resumed_clients[0].requests[0]))

    def test_different_sessions_can_run_concurrently(self) -> None:
        started = [ThreadEvent(), ThreadEvent()]
        release = ThreadEvent()
        clients: list[ConcurrentClient] = []

        def client_factory() -> ConcurrentClient:
            client = ConcurrentClient(started[len(clients)], release)
            clients.append(client)
            return client

        with tempfile.TemporaryDirectory() as directory:
            runtime = AgentRuntime(_settings(Path(directory)), client_factory)
            first_id = runtime.new_conversation()
            second_id = runtime.new_conversation()
            first = Thread(target=lambda: runtime.ask("第一会话", first_id), daemon=True)
            second = Thread(target=lambda: runtime.ask("第二会话", second_id), daemon=True)
            try:
                first.start()
                second.start()
                self.assertTrue(started[0].wait(timeout=1))
                self.assertTrue(started[1].wait(timeout=1))
                release.set()
                first.join(timeout=1)
                second.join(timeout=1)
            finally:
                release.set()
                runtime.close()

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())

    def test_http_page_creates_and_uses_a_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                api_key=None,
                model="test-model",
                base_url="http://unused",
                system_prompt="test",
                workspace=Path(directory),
                shell_enabled=False,
                request_timeout_seconds=1,
                max_tool_rounds=1,
                session_db_path=Path(directory) / "sessions.db",
            )
            runtime = AgentRuntime(settings, RecordingClient)
            server = AgentHTTPServer(("127.0.0.1", 0), runtime)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            address = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(address) as response:
                    self.assertIn("CodeX Agent", response.read().decode("utf-8"))

                created = _post_json(f"{address}/api/new", {})
                self.assertEqual(created.get("ok"), True)
                response = _post_json(
                    f"{address}/api/messages",
                    {"text": "网页消息", "mode": "plan"},
                )
                with urlopen(f"{address}/api/sessions/{created['id']}") as detail_response:
                    detail = json.loads(detail_response.read())
                with urlopen(f"{address}/api/metrics") as metrics_response:
                    metrics = json.loads(metrics_response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                runtime.close()

        self.assertEqual(response["events"][1]["text"], "收到：网页消息")
        self.assertEqual(response["events"][0]["data"]["mode"], "plan")
        self.assertEqual(detail["session"]["mode"], "plan")
        self.assertEqual(metrics["turns"]["finished"], 1)

    def test_http_stream_forwards_assistant_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                api_key=None,
                model="test-model",
                base_url="http://unused",
                system_prompt="test",
                workspace=Path(directory),
                shell_enabled=False,
                request_timeout_seconds=1,
                max_tool_rounds=1,
                session_db_path=Path(directory) / "sessions.db",
            )
            runtime = AgentRuntime(settings, StreamingClient)
            server = AgentHTTPServer(("127.0.0.1", 0), runtime)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            address = f"http://127.0.0.1:{server.server_port}"
            try:
                self.assertEqual(_post_json(f"{address}/api/new", {}).get("ok"), True)
                events = _post_sse(f"{address}/api/messages/stream", {"text": "网页消息"})
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                runtime.close()

        self.assertEqual([event["kind"] for event in events], [
            EventKind.TURN_STARTED.value,
            EventKind.ASSISTANT_MESSAGE.value,
            EventKind.ASSISTANT_MESSAGE.value,
            EventKind.ASSISTANT_MESSAGE.value,
            EventKind.TURN_FINISHED.value,
        ])
        self.assertEqual([event["text"] for event in events[1:3]], ["逐", "字"])
        self.assertTrue(all(event["data"] == {"delta": True} for event in events[1:3]))
        self.assertEqual(events[3]["data"], {"replace": True, "is_final": True})

    def test_http_approval_resumes_the_waiting_shell_call_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                api_key=None,
                model="test-model",
                base_url="http://unused",
                system_prompt="test",
                workspace=Path(directory),
                shell_enabled=True,
                request_timeout_seconds=1,
                max_tool_rounds=2,
                session_db_path=Path(directory) / "sessions.db",
            )
            runtime = AgentRuntime(settings, EscalatingWebClient)
            server = AgentHTTPServer(("127.0.0.1", 0), runtime)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            address = f"http://127.0.0.1:{server.server_port}"
            host_process = AsyncMock(return_value=_CompletedProcess())
            try:
                with patch(
                    "agent.tools.handlers.exec_command._native_windows_available",
                    return_value=True,
                ), patch(
                    "agent.tools.handlers.exec_command.asyncio.create_subprocess_shell",
                    new=host_process,
                ):
                    session_id = _post_json(f"{address}/api/sessions", {})["id"]
                    events = _post_sse_with_approval(address, str(session_id))
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                runtime.close()

        self.assertIn(EventKind.APPROVAL_REQUESTED.value, [event["kind"] for event in events])
        self.assertEqual(events[-1]["kind"], EventKind.TURN_FINISHED.value)
        host_process.assert_awaited_once()

    def test_http_session_api_lists_and_loads_persisted_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = AgentRuntime(_settings(Path(directory)), RecordingClient)
            server = AgentHTTPServer(("127.0.0.1", 0), runtime)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            address = f"http://127.0.0.1:{server.server_port}"
            try:
                created = _post_json(f"{address}/api/sessions", {})
                session_id = created["id"]
                _post_json(
                    f"{address}/api/sessions/{session_id}/messages",
                    {"text": "持久化消息"},
                )
                sessions = _get_json(f"{address}/api/sessions")["sessions"]
                detail = _get_json(f"{address}/api/sessions/{session_id}")
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                runtime.close()

        self.assertEqual(sessions[0]["id"], session_id)
        self.assertEqual(sessions[0]["title"], "持久化消息")
        self.assertEqual(
            [message["text"] for message in detail["messages"]],
            ["持久化消息", "收到：持久化消息"],
        )

    def test_http_permissions_can_be_read_and_switched_while_idle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = AgentRuntime(_settings(Path(directory)), RecordingClient)
            server = AgentHTTPServer(("127.0.0.1", 0), runtime)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            address = f"http://127.0.0.1:{server.server_port}"
            try:
                _post_json(f"{address}/api/sessions", {})
                initial = _get_json(f"{address}/api/permissions")
                updated = _post_json(
                    f"{address}/api/permissions",
                    {"sandbox_mode": "read-only", "confirmed": False},
                )
                with self.assertRaises(HTTPError) as rejected:
                    _post_json(
                        f"{address}/api/permissions",
                        {"sandbox_mode": "danger-full-access"},
                    )
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                runtime.close()

        self.assertEqual(initial["sandbox_mode"], "workspace-write")
        self.assertEqual(updated["sandbox_mode"], "read-only")
        self.assertEqual(rejected.exception.code, 400)
        self.assertTrue(_is_loopback_client("127.0.0.1"))
        self.assertTrue(_is_loopback_client("::ffff:127.0.0.1"))
        self.assertFalse(_is_loopback_client("192.0.2.1"))

    def test_http_configuration_reloads_runtime_without_exposing_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "vault"
            workspace.mkdir()
            runtime = AgentRuntime(_settings(root), RecordingClient)
            server = AgentHTTPServer(("127.0.0.1", 0), runtime)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            address = f"http://127.0.0.1:{server.server_port}"
            session_db = root / "configured" / "sessions.db"
            try:
                initial = _get_json(f"{address}/api/config")
                updated = _post_json(
                    f"{address}/api/config",
                    {
                        "api_key": "secret-value",
                        "base_url": "https://example.com/v1/",
                        "model": "configured-model",
                        "workspace": str(workspace),
                        "sandbox_mode": "read-only",
                        "approval_policy": "never",
                        "shell_enabled": False,
                        "session_db_path": str(session_db),
                        "confirmed": False,
                    },
                )
                with self.assertRaises(HTTPError) as rejected:
                    _post_json(
                        f"{address}/api/config",
                        {"sandbox_mode": "danger-full-access", "confirmed": False},
                    )
                created = _post_json(f"{address}/api/sessions", {})
                db_created = session_db.is_file()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                runtime.close()

        self.assertFalse(initial["api_key_configured"])
        self.assertTrue(updated["api_key_configured"])
        self.assertNotIn("api_key", updated)
        self.assertEqual(updated["model"], "configured-model")
        self.assertEqual(updated["base_url"], "https://example.com/v1")
        self.assertEqual(updated["sandbox_mode"], "read-only")
        self.assertEqual(rejected.exception.code, 400)
        self.assertEqual(created["workspace"], str(workspace))
        self.assertTrue(db_created)

    def test_runtime_rejects_permission_switch_during_a_turn(self) -> None:
        started = ThreadEvent()
        release = ThreadEvent()
        with tempfile.TemporaryDirectory() as directory:
            runtime = AgentRuntime(
                _settings(Path(directory)),
                lambda: ConcurrentClient(started, release),
            )
            runtime.new_conversation()
            turn = Thread(target=lambda: runtime.ask("保持运行"), daemon=True)
            turn.start()
            try:
                self.assertTrue(started.wait(timeout=1))
                with self.assertRaisesRegex(RuntimeError, "运行中的回合"):
                    runtime.update_permissions(SandboxMode.READ_ONLY)
            finally:
                release.set()
                turn.join(timeout=1)
                runtime.close()

    def test_new_message_interrupts_an_active_web_stream(self) -> None:
        clients: list[SteeringClient] = []

        def client_factory() -> SteeringClient:
            client = SteeringClient()
            clients.append(client)
            return client

        with tempfile.TemporaryDirectory() as directory:
            runtime = AgentRuntime(_settings(Path(directory)), client_factory)
            first_events: list[object] = []
            try:
                runtime.new_conversation()
                first_stream = Thread(
                    target=lambda: first_events.extend(runtime.ask_events("先用 Go 实现")), daemon=True
                )
                first_stream.start()
                self.assertTrue(clients[0].started.wait(timeout=1))

                second_events = runtime.ask("改用 Python")
                first_stream.join(timeout=1)
            finally:
                runtime.close()

        self.assertFalse(first_stream.is_alive())
        self.assertTrue(clients[0].was_cancelled)
        self.assertIn(EventKind.TURN_INTERRUPTED, [event.kind for event in first_events])
        self.assertEqual([event.kind for event in second_events], [
            EventKind.TURN_STARTED,
            EventKind.ASSISTANT_MESSAGE,
            EventKind.TURN_FINISHED,
        ])
        user_messages = [message["content"] for message in clients[0].requests[1] if message["role"] == "user"]
        self.assertEqual(user_messages, ["先用 Go 实现", "改用 Python"])


def _post_json(url: str, body: dict[str, object]) -> dict[str, object]:
    """通过真实 HTTP 请求覆盖页面所使用的同源 API。"""

    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request) as response:
        return json.loads(response.read())


def _get_json(url: str) -> dict[str, object]:
    with urlopen(url) as response:
        return json.loads(response.read())


def _settings(workspace: Path) -> Settings:
    return Settings(
        api_key=None,
        model="test-model",
        base_url="http://unused",
        system_prompt="test",
        workspace=workspace,
        shell_enabled=False,
        request_timeout_seconds=1,
        max_tool_rounds=1,
        session_db_path=workspace / "sessions.db",
    )


def _post_sse(url: str, body: dict[str, object]) -> list[dict[str, object]]:
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request) as response:
        raw_events = response.read().decode("utf-8").splitlines()
    return [json.loads(line[5:].strip()) for line in raw_events if line.startswith("data:")]


class _CompletedProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.stdout = self
        self.stdin = _CompletedStdin()
        self._output = b"ok"

    async def read(self, size: int = 65_536) -> bytes:
        output, self._output = self._output, b""
        return output

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = -9


class _CompletedStdin:
    def is_closing(self) -> bool:
        return False

    def write(self, data: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None


def _post_sse_with_approval(address: str, session_id: str) -> list[dict[str, object]]:
    request = Request(
        f"{address}/api/sessions/{session_id}/messages/stream",
        data=json.dumps({"text": "运行提权命令"}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    events: list[dict[str, object]] = []
    with urlopen(request) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8")
            if not line.startswith("data:"):
                continue
            event = json.loads(line[5:].strip())
            events.append(event)
            if event["kind"] == EventKind.APPROVAL_REQUESTED.value:
                data = event["data"]
                _post_json(
                    f"{address}/api/sessions/{session_id}/approvals",
                    {
                        "call_id": data["call_id"],
                        "approved": True,
                        "submission_id": data["submission_id"],
                    },
                )
    return events
