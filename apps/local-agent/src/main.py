"""Obsidian 本地 Agent：配对、持久会话、SSE 与安全操作计划接口。"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import secrets
from threading import Lock
from typing import Any, Mapping
from urllib.parse import urlparse

from agent.protocol.event import Event, EventKind
from agent.protocol.mode import ModeKind
from agent.obsidian.container import LocalAgentContainer, build_container
from agent.obsidian.settings import LocalAgentSettings, load_settings
from agent.utils.logging import configure_logging


LOGGER = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 1_000_000
TERMINAL_EVENTS = {
    EventKind.TURN_FINISHED,
    EventKind.TURN_INTERRUPTED,
    EventKind.ERROR,
    EventKind.SHUTDOWN,
}


class LocalAgentHandler(BaseHTTPRequestHandler):
    container: LocalAgentContainer | None = None
    settings: LocalAgentSettings
    token: str | None = None
    vault_root: Path | None = None
    paired = False
    pairing_lock = Lock()

    def do_OPTIONS(self) -> None:
        if urlparse(self.path).path.endswith("/messages/stream"):
            self._send_stream_preflight()
            return
        self._send_json({"error": "browser access is not allowed"}, status=403)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json({"status": "ok", "configured": self.container is not None})
            return
        if not self._require_ready() or not self._require_token():
            return
        assert self.container is not None
        if path == "/identity":
            self._send_json({
                "vaultRoot": str(self.vault_root),
                "executionMode": self.container.operations.policy.mode.value,
            })
            return
        if path == "/tools":
            self._send_json({"tools": [_jsonable(tool.definition) for tool in self.container.tools]})
            return
        if path == "/api/sessions":
            self._send_json({"sessions": self.container.runtime.conversations()})
            return
        route = _session_route(path)
        if route is not None and route[1] == "":
            try:
                detail = self.container.runtime.conversation(route[0])
                detail["pendingOperationPlan"] = _jsonable(asyncio.run(
                    self.container.operations.store.latest_pending_for_conversation(route[0])
                ))
                self._send_json(detail)
            except KeyError as exc:
                self._send_json({"error": str(exc)}, status=404)
            return
        if path.startswith("/operations/"):
            try:
                plan_id = _operation_id(path)
                self._send_json(_jsonable(asyncio.run(self.container.operations.store.get(plan_id))))
            except KeyError as exc:
                self._send_json({"error": str(exc)}, status=404)
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/handshake":
            self._handshake()
            return
        if not self._require_ready() or not self._require_token():
            return
        assert self.container is not None
        if path == "/policy":
            self._update_policy()
            return
        if path == "/api/sessions":
            session_id = self.container.runtime.new_conversation()
            self._send_json(self.container.runtime.conversation(session_id)["session"], status=201)
            return
        route = _session_route(path)
        if route is not None and route[1] == "messages/stream":
            self._stream_session(route[0])
            return
        if route is not None and route[1] == "approvals":
            self._resolve_approval(route[0])
            return
        if route is not None and route[1] == "archive":
            try:
                self.container.runtime.archive_conversation(route[0])
                self._send_json({"ok": True})
            except KeyError as exc:
                self._send_json({"error": str(exc)}, status=404)
            return
        if path == "/operations":
            self._stage_operation()
            return
        if path.startswith("/operations/") and path.endswith("/execute"):
            self._execute_operation(path)
            return
        if path.startswith("/operations/") and path.endswith("/rollback"):
            self._rollback_operation(path)
            return
        self._send_json({"error": "not found"}, status=404)

    def _stream_session(self, session_id: str) -> None:
        streaming = False
        submission_id: int | None = None
        try:
            payload = self._read_json()
            text = _text(payload, "text")
            mode = ModeKind.parse(str(payload.get("mode") or "default"))
            metadata = _turn_metadata(payload.get("context"), text)
            self._send_stream_headers()
            streaming = True
            assert self.container is not None
            for event in self.container.runtime.ask_events(text, session_id, mode, metadata):
                if event.kind is EventKind.TURN_STARTED:
                    value = event.data.get("submission_id")
                    submission_id = value if isinstance(value, int) else None
                self._send_stream_value(_event_json(event))
                plan = _operation_plan_from(event)
                if plan is not None:
                    self._send_stream_value({"kind": "operation_plan", "text": "", "data": {"plan": plan}})
                if event.kind in TERMINAL_EVENTS:
                    return
        except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            if streaming:
                self._send_stream_value({"kind": "error", "text": str(exc), "data": {}})
            else:
                self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            LOGGER.exception("session stream failed")
            if streaming:
                self._send_stream_value({"kind": "error", "text": str(exc), "data": {}})
            else:
                self._send_json({"error": str(exc)}, status=500)
        finally:
            if self.container is not None:
                self.container.access_ledger.clear(session_id, submission_id)

    def _resolve_approval(self, session_id: str) -> None:
        try:
            payload = self._read_json()
            call_id = _text(payload, "callId", "call_id")
            approved = payload.get("approved")
            submission_id = payload.get("submissionId", payload.get("submission_id"))
            if not isinstance(approved, bool) or not isinstance(submission_id, int):
                raise ValueError("approved and submissionId are required")
            assert self.container is not None
            if not self.container.runtime.resolve_approval(call_id, approved, submission_id, session_id):
                raise ValueError("approval request is no longer active")
            self._send_json({"ok": True})
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=409)

    def _handshake(self) -> None:
        try:
            if self.headers.get("Origin"):
                self._send_json({"error": "browser handshake is not allowed"}, status=403)
                return
            with self.pairing_lock:
                if self.paired:
                    self._send_json({"error": "local-agent is already paired; restart it to pair again"}, status=409)
                    return
                payload = self._read_json()
                vault_root = Path(_text(payload, "vaultPath")).expanduser().resolve()
                if not vault_root.is_dir() or not (vault_root / ".obsidian").is_dir():
                    raise ValueError("vault path must be an Obsidian vault")
                if self.vault_root is not None and vault_root != self.vault_root:
                    raise ValueError("local-agent is already bound to another vault")
                configured = LocalAgentSettings(
                    host=self.settings.host,
                    port=self.settings.port,
                    vault_root=vault_root,
                    execution_mode=str(payload.get("executionMode") or self.settings.execution_mode),
                    **_llm_settings(payload, self.settings),
                )
                if self.container is None:
                    self.__class__.container = build_container(configured)
                self.__class__.vault_root = vault_root
                self.__class__.token = secrets.token_urlsafe(24)
                self.__class__.paired = True
            LOGGER.info("local-agent bound to vault root: %s", vault_root)
            self._send_json({
                "status": "ok",
                "port": self.server.server_address[1],
                "token": self.token,
                "vaultRoot": str(vault_root),
            })
        except Exception as exc:
            LOGGER.exception("local-agent handshake failed")
            self._send_json({"error": str(exc)}, status=400)

    def _update_policy(self) -> None:
        try:
            payload = self._read_json()
            mode = _text(payload, "executionMode")
            assert self.container is not None
            if "llm" in payload and self.vault_root is not None:
                configured = LocalAgentSettings(
                    host=self.settings.host,
                    port=self.settings.port,
                    vault_root=self.vault_root,
                    execution_mode=mode,
                    **_llm_settings(payload, self.settings),
                )
                replacement = build_container(configured)
                previous = self.container
                self.__class__.container = replacement
                previous.runtime.close()
            else:
                self.container.operations.set_mode(mode)
            assert self.container is not None
            self._send_json({"executionMode": self.container.operations.policy.mode.value})
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=400)

    def _stage_operation(self) -> None:
        try:
            payload = self._read_json()
            assert self.container is not None
            self._send_json(_jsonable(asyncio.run(self.container.operations.stage(payload))), status=201)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=400)

    def _execute_operation(self, path: str) -> None:
        try:
            payload = self._read_json()
            plan_id = _operation_id(path, suffix="/execute")
            token = _optional_text(payload.get("confirmationToken"))
            assert self.container is not None
            result = asyncio.run(self.container.operations.execute(plan_id, confirmation_token=token))
            self._send_json(_jsonable(result))
        except PermissionError as exc:
            self._send_json({"error": str(exc)}, status=403)
        except (KeyError, ValueError) as exc:
            self._send_json({"error": str(exc)}, status=409)

    def _rollback_operation(self, path: str) -> None:
        try:
            payload = self._read_json()
            plan_id = _operation_id(path, suffix="/rollback")
            token = _optional_text(payload.get("rollbackToken"))
            assert self.container is not None
            result = asyncio.run(self.container.operations.rollback(plan_id, confirmation_token=token))
            self._send_json(_jsonable(result))
        except PermissionError as exc:
            self._send_json({"error": str(exc)}, status=403)
        except (KeyError, ValueError) as exc:
            self._send_json({"error": str(exc)}, status=409)

    def _require_ready(self) -> bool:
        if self.container is not None:
            return True
        self._send_json({"error": "local-agent is waiting for handshake"}, status=409)
        return False

    def _require_token(self) -> bool:
        expected = self.token
        actual = self.headers.get("X-Agent-Token", "")
        if expected and secrets.compare_digest(actual, expected):
            return True
        self._send_json({"error": "invalid local-agent token"}, status=403)
        return False

    def _read_json(self) -> dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0"))
        if size < 0 or size > MAX_REQUEST_BYTES:
            raise ValueError("request body is too large")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json")
        value = json.loads(self.rfile.read(size).decode("utf-8") or "{}")
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _send_json(self, value: Any, *, status: int = 200) -> None:
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _send_stream_preflight(self) -> None:
        self.send_response(204)
        self._send_stream_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Agent-Token")
        self.end_headers()

    def _send_stream_headers(self) -> None:
        self.send_response(200)
        self._send_stream_cors_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

    def _send_stream_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin") or "*")
        self.send_header("Vary", "Origin")

    def _send_stream_value(self, value: Mapping[str, Any]) -> None:
        raw = f"data: {json.dumps(value, ensure_ascii=False)}\n\n".encode("utf-8")
        self.wfile.write(raw)
        self.wfile.flush()

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.debug(format, *args)


def main() -> None:
    configure_logging("INFO", "text")
    settings = load_settings()
    if settings.host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("local-agent must bind to localhost")
    LocalAgentHandler.settings = settings
    if settings.vault_root is not None and settings.llm_base_url and settings.llm_model:
        LocalAgentHandler.container = build_container(settings)
        LocalAgentHandler.vault_root = settings.vault_root
        LocalAgentHandler.token = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer((settings.host, settings.port), LocalAgentHandler)
    LOGGER.info("local-agent listening on http://%s:%s", settings.host, settings.port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if LocalAgentHandler.container is not None:
            LocalAgentHandler.container.runtime.close()


def _session_route(path: str) -> tuple[str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) < 3 or parts[:2] != ["api", "sessions"] or not parts[2] or len(parts[2]) > 64:
        return None
    return parts[2], "/".join(parts[3:])


def _turn_metadata(value: object, text: str) -> dict[str, object]:
    context = dict(value) if isinstance(value, Mapping) else {}
    scope = str(context.get("scope") or "vault")
    if scope not in {"current", "vault"}:
        raise ValueError("context.scope must be current or vault")
    result: dict[str, object] = {"scope": scope, "userInput": text}
    for key in ("activeFilePath", "selectedText"):
        item = context.get(key)
        if isinstance(item, str) and item:
            result[key] = item
    referenced = context.get("referencedPaths")
    if isinstance(referenced, list):
        result["referencedPaths"] = [item for item in referenced[:32] if isinstance(item, str) and item]
    tasks = context.get("tasks")
    if isinstance(tasks, list):
        result["tasks"] = tasks[:500]
    return result


def _operation_plan_from(event: Event) -> Mapping[str, Any] | None:
    if event.kind is not EventKind.TOOL_RESULT or event.data.get("name") != "build_operation_plan":
        return None
    try:
        payload = json.loads(event.text)
    except (TypeError, json.JSONDecodeError):
        return None
    plan = payload.get("plan") if isinstance(payload, Mapping) else None
    return plan if isinstance(plan, Mapping) else None


def _event_json(event: Event) -> dict[str, Any]:
    return {"kind": event.kind.value, "text": event.text, "data": event.data}


def _text(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"one of {', '.join(keys)} is required")


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _operation_id(path: str, *, suffix: str = "") -> str:
    clean = path.removesuffix(suffix).removeprefix("/operations/").strip("/")
    if not clean or "/" in clean:
        raise ValueError("invalid operation plan path")
    return clean


def _llm_settings(payload: Mapping[str, Any], fallback: LocalAgentSettings) -> dict[str, str | None]:
    config = payload.get("llm")
    if not isinstance(config, Mapping):
        return {
            "llm_base_url": fallback.llm_base_url,
            "llm_model": fallback.llm_model,
            "llm_api_key": fallback.llm_api_key,
        }
    return {
        "llm_base_url": _optional_text(config.get("baseUrl")) or fallback.llm_base_url,
        "llm_model": _optional_text(config.get("model")) or fallback.llm_model,
        "llm_api_key": _optional_text(config.get("apiKey")) or fallback.llm_api_key,
    }


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


if __name__ == "__main__":
    main()
