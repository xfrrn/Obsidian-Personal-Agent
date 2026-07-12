from __future__ import annotations

import json
from datetime import date
from pathlib import Path
import sys
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [
    str(ROOT),
    str(ROOT / "packages"),
    str(ROOT / "packages" / "agent-core"),
    str(ROOT / "apps" / "local-agent" / "src"),
]

from bootstrap.settings import LocalAgentSettings  # noqa: E402
from bootstrap.container import _agent_chat_decision  # noqa: E402
from main import LocalAgentHandler, ThreadingHTTPServer, _intent_llm_settings  # noqa: E402


def test_handshake_agent_llm_settings_override_env_fallback() -> None:
    fallback = LocalAgentSettings(
        "127.0.0.1",
        0,
        intent_llm_base_url="https://env.example/v1",
        intent_llm_model="env-model",
        intent_llm_api_key="env-key",
    )
    assert _intent_llm_settings({"intentLlm": {"baseUrl": "https://plugin.example/v1", "model": "plugin-model"}}, fallback) == {
        "intent_llm_base_url": "https://plugin.example/v1",
        "intent_llm_model": "plugin-model",
        "intent_llm_api_key": "env-key",
    }


def test_agent_llm_reads_tool_calls() -> None:
    decision = _agent_chat_decision({
        "choices": [{
            "message": {
                "tool_calls": [{
                    "id": "call_1",
                    "function": {"name": "list_tasks", "arguments": "{\"status\":\"open\"}"},
                }]
            }
        }]
    })

    assert decision["tool_calls"][0]["function"]["name"] == "list_tasks"


def test_handshake_rejects_browsers_and_cannot_rebind(tmp_path: Path) -> None:
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / "Tasks.md").write_text(f"- [ ] write tests 📅 {date.today().isoformat()}\n", encoding="utf-8")
    LocalAgentHandler.container = None
    LocalAgentHandler.token = None
    LocalAgentHandler.vault_root = None
    LocalAgentHandler.paired = False
    LocalAgentHandler.settings = LocalAgentSettings("127.0.0.1", 0)
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalAgentHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    body = {"vaultPath": str(tmp_path)}

    try:
        status, headers, _ = _request(base + "/handshake", body, {"Origin": "https://evil.example"})
        assert status == 403
        assert "Access-Control-Allow-Origin" not in headers

        status, _, paired = _request(base + "/handshake", body)
        assert status == 200
        token = paired["token"]

        status, _, health = _request(base + "/health")
        assert status == 200 and "vaultRoot" not in health
        assert _request(base + "/tools")[0] == 403
        status, _, tools = _request(base + "/tools", headers={"X-Agent-Token": token})
        assert status == 200
        tool_names = {item["name"] for item in tools["tools"]}
        assert {"inspect_note", "analyze_project", "check_vault_health", "find_duplicates"} <= tool_names
        assert _request(base + "/identity", headers={"X-Agent-Token": token})[2]["vaultRoot"] == str(tmp_path)

        status, _, chat = _request(base + "/chat", {"userInput": "query tasks"}, {"X-Agent-Token": token})
        assert status == 500
        assert "agent LLM is not configured" in chat["error"]

        assert _request(
            base + "/chat",
            {"userInput": "confirm", "trigger": "operation_confirmed", "operationPlanId": "fake"},
            {"X-Agent-Token": token},
        )[0] == 400

        operation = {
            "summary": "http test",
            "operations": [{"type": "create-note", "path": "00-Inbox/HTTP.md", "content": "ok\n"}],
            "context": {"source": "interactive"},
        }
        status, _, staged = _request(base + "/operations", operation, {"X-Agent-Token": token})
        assert status == 201
        plan_id = staged["planId"]
        assert isinstance(plan_id, str)
        assert staged["requiresConfirmation"]
        confirmation = staged["confirmationToken"]
        assert _request(base + f"/operations/{plan_id}/execute", {}, {"X-Agent-Token": token})[0] == 403
        status, _, executed = _request(
            base + f"/operations/{plan_id}/execute",
            {"confirmationToken": confirmation},
            {"X-Agent-Token": token},
        )
        assert status == 200 and executed["status"] == "succeeded"
        rollback_token = executed["rollbackToken"]
        assert (tmp_path / "00-Inbox" / "HTTP.md").exists()
        status, _, rolled_back = _request(
            base + f"/operations/{plan_id}/rollback",
            {"rollbackToken": rollback_token},
            {"X-Agent-Token": token},
        )
        assert status == 200 and rolled_back["status"] == "rolled_back"
        assert not (tmp_path / "00-Inbox" / "HTTP.md").exists()
        assert _request(
            base + "/policy",
            {
                "executionMode": "risk_based",
                "intentLlm": {"baseUrl": "http://127.0.0.1:1/v1", "model": "test-model"},
            },
            {"X-Agent-Token": token},
        )[2]["executionMode"] == "risk_based"
        assert _request(base + "/identity", headers={"X-Agent-Token": token})[2]["executionMode"] == "risk_based"
        assert _request(base + "/handshake", body)[0] == 409
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        LocalAgentHandler.container = None
        LocalAgentHandler.token = None
        LocalAgentHandler.vault_root = None
        LocalAgentHandler.paired = False


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
