from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent.protocol.event import Event, EventKind
from agent.protocol.mode import ModeKind
from agent.obsidian.container import _validated_base_url
from agent.obsidian.settings import LocalAgentSettings
from main import LocalAgentHandler, ThreadingHTTPServer, _llm_settings


def test_handshake_llm_settings_override_env_fallback() -> None:
    fallback = LocalAgentSettings(
        "127.0.0.1",
        0,
        llm_base_url="https://env.example/v1",
        llm_model="env-model",
        llm_api_key="env-key",
    )
    assert _llm_settings(
        {"llm": {"baseUrl": "https://plugin.example/v1", "model": "plugin-model"}},
        fallback,
    ) == {
        "llm_base_url": "https://plugin.example/v1",
        "llm_model": "plugin-model",
        "llm_api_key": "env-key",
    }


def test_model_endpoint_rejects_insecure_remote_urls() -> None:
    assert _validated_base_url("http://127.0.0.1:11434/v1/") == "http://127.0.0.1:11434/v1"
    try:
        _validated_base_url("http://example.com/v1")
    except ValueError as error:
        assert "HTTPS" in str(error)
    else:
        raise AssertionError("remote model endpoints must use HTTPS")


def test_handshake_auth_sessions_and_operation_lifecycle(tmp_path: Path) -> None:
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / "Tasks.md").write_text(
        f"- [ ] write tests 📅 {date.today().isoformat()}\n", encoding="utf-8"
    )
    _reset_handler(LocalAgentSettings("127.0.0.1", 0))
    server, thread, base = _serve()
    body = {
        "vaultPath": str(tmp_path),
        "llm": {
            "baseUrl": "http://127.0.0.1:1/v1",
            "model": "test-model",
            "apiKey": "memory-only-secret",
        },
    }

    try:
        status, headers, _ = _request(base + "/handshake", body, {"Origin": "https://evil.example"})
        assert status == 403
        assert "Access-Control-Allow-Origin" not in headers

        status, _, paired = _request(base + "/handshake", body)
        assert status == 200
        token = paired["token"]
        auth = {"X-Agent-Token": token}

        assert _request(base + "/api/sessions")[0] == 403
        status, _, created = _request(base + "/api/sessions", {}, auth)
        assert status == 201
        session_id = created["id"]
        assert _request(base + "/api/sessions", headers=auth)[2]["sessions"][0]["id"] == session_id
        detail = _request(base + f"/api/sessions/{session_id}", headers=auth)[2]
        assert detail["session"]["id"] == session_id
        assert detail["pendingOperationPlan"] is None

        tools = _request(base + "/tools", headers=auth)[2]["tools"]
        names = {item["name"] for item in tools}
        assert {"search_notes", "read_note", "build_operation_plan"} <= names
        assert not {"apply_patch", "exec_command", "write_stdin"} & names

        operation = {
            "summary": "http test",
            "operations": [{"type": "create-note", "path": "00-Inbox/HTTP.md", "content": "ok\n"}],
            "context": {"source": "interactive"},
        }
        status, _, staged = _request(base + "/operations", operation, auth)
        assert status == 201 and staged["requiresConfirmation"]
        plan_id = staged["planId"]
        assert _request(base + f"/operations/{plan_id}/execute", {}, auth)[0] == 403
        executed = _request(
            base + f"/operations/{plan_id}/execute",
            {"confirmationToken": staged["confirmationToken"]},
            auth,
        )[2]
        assert executed["status"] == "succeeded"
        assert (tmp_path / "00-Inbox" / "HTTP.md").exists()
        rolled_back = _request(
            base + f"/operations/{plan_id}/rollback",
            {"rollbackToken": executed["rollbackToken"]},
            auth,
        )[2]
        assert rolled_back["status"] == "rolled_back"
        assert not (tmp_path / "00-Inbox" / "HTTP.md").exists()

        assert _request(base + f"/api/sessions/{session_id}/archive", {}, auth)[0] == 200
        assert _request(base + f"/api/sessions/{session_id}", headers=auth)[0] == 404
        assert _request(base + "/handshake", body)[0] == 409
    finally:
        _stop(server, thread)


def test_session_stream_forwards_events_context_and_operation_plan() -> None:
    class FakeLedger:
        cleared: tuple[str | None, int | None] | None = None

        def clear(self, session_id: str | None, submission_id: int | None) -> None:
            self.cleared = (session_id, submission_id)

    class FakeRuntime:
        received: tuple[str, str, ModeKind, object] | None = None

        def ask_events(self, text: str, session_id: str, mode: ModeKind, metadata: object):
            self.received = (text, session_id, mode, metadata)
            yield Event(EventKind.TURN_STARTED, data={"submission_id": 7, "mode": mode.value})
            yield Event(
                EventKind.TOOL_CALL,
                "build_operation_plan",
                {"call_id": "call-1", "arguments": {"requestedOperations": []}},
            )
            yield Event(
                EventKind.TOOL_RESULT,
                json.dumps({"plan": {"planId": "op-1", "summary": "preview"}}),
                {"call_id": "call-1", "name": "build_operation_plan"},
            )
            yield Event(EventKind.ASSISTANT_MESSAGE, "done", {"replace": True, "is_final": True})
            yield Event(EventKind.TURN_FINISHED)

    runtime = FakeRuntime()
    ledger = FakeLedger()
    _reset_handler(LocalAgentSettings("127.0.0.1", 0))
    LocalAgentHandler.container = SimpleNamespace(runtime=runtime, access_ledger=ledger)
    LocalAgentHandler.token = "token"
    LocalAgentHandler.paired = True
    server, thread, base = _serve()

    try:
        body = json.dumps({
            "text": "plan it",
            "mode": "plan",
            "context": {
                "scope": "current",
                "activeFilePath": "Current.md",
                "referencedPaths": ["Referenced.md"],
            },
        }).encode()
        request = Request(
            base + "/api/sessions/session-1/messages/stream",
            data=body,
            headers={"Content-Type": "application/json", "X-Agent-Token": "token"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            text = response.read().decode()
        events = [json.loads(frame.removeprefix("data: ")) for frame in text.strip().split("\n\n")]
        assert [event["kind"] for event in events] == [
            "turn_started",
            "tool_call",
            "tool_result",
            "operation_plan",
            "assistant_message",
            "turn_finished",
        ]
        assert runtime.received is not None
        assert runtime.received[2] is ModeKind.PLAN
        assert runtime.received[3]["activeFilePath"] == "Current.md"
        assert ledger.cleared == ("session-1", 7)
    finally:
        _stop(server, thread, close_runtime=False)


def _serve() -> tuple[ThreadingHTTPServer, Thread, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAgentHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


def _stop(server: ThreadingHTTPServer, thread: Thread, *, close_runtime: bool = True) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    container = LocalAgentHandler.container
    if close_runtime and container is not None:
        container.runtime.close()
    _reset_handler(LocalAgentSettings("127.0.0.1", 0))


def _reset_handler(settings: LocalAgentSettings) -> None:
    LocalAgentHandler.container = None
    LocalAgentHandler.token = None
    LocalAgentHandler.vault_root = None
    LocalAgentHandler.paired = False
    LocalAgentHandler.settings = settings


def _request(
    url: str,
    body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, object, dict[str, object]]:
    data = json.dumps(body).encode() if body is not None else None
    request = Request(url, data=data, headers=headers or {}, method="POST" if body is not None else "GET")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        response = urlopen(request, timeout=2)
    except HTTPError as error:
        response = error
    with response:
        payload = json.loads(response.read().decode())
        return response.status, response.headers, payload
