"""Local Agent service entrypoint."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
import json
import logging
from pathlib import Path
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from typing import Any
from urllib.parse import urlparse

from bootstrap.container import LocalAgentContainer, build_container
from bootstrap.settings import LocalAgentSettings, load_settings
from infrastructure.logging import configure_logging
from runtime import RuntimeRequest, RuntimeTrigger

LOGGER = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 1_000_000


class LocalAgentHandler(BaseHTTPRequestHandler):
    container: LocalAgentContainer | None = None
    settings: LocalAgentSettings
    token: str | None = None
    vault_root: Path | None = None
    paired = False
    pairing_lock = Lock()

    def do_OPTIONS(self) -> None:
        if urlparse(self.path).path == "/chat/stream":
            self._send_stream_preflight()
            return
        self._send_json({"error": "browser access is not allowed"}, status=403)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json({
                "status": "ok",
                "configured": self.container is not None,
            })
            return
        if path == "/tools":
            if not self._require_ready() or not self._require_token():
                return
            self._send_json({"tools": [_jsonable(tool) for tool in self.container.registry.definitions()]})
            return
        if path == "/identity":
            if not self._require_ready() or not self._require_token():
                return
            assert self.container is not None
            self._send_json({
                "vaultRoot": str(self.vault_root),
                "executionMode": self.container.operations.policy.mode.value,
            })
            return
        if path.startswith("/operations/"):
            if not self._require_ready() or not self._require_token():
                return
            plan_id = _operation_id(path)
            assert self.container is not None
            try:
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
        if path == "/policy":
            self._update_policy()
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
        if path == "/chat/stream":
            self._stream_chat()
            return
        if path != "/chat":
            self._send_json({"error": "not found"}, status=404)
            return
        try:
            if not self._require_ready() or not self._require_token():
                return
            payload = self._read_json()
            assert self.container is not None
            result = asyncio.run(self.container.runtime.run(_chat_request(payload)))
            self._send_json(_jsonable(result))
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            LOGGER.exception("local-agent request failed")
            self._send_json({"error": str(exc)}, status=500)

    def _stream_chat(self) -> None:
        streaming = False
        try:
            if not self._require_ready() or not self._require_token():
                return
            payload = self._read_json()
            request = _chat_request(payload)
            self._send_stream_headers()
            streaming = True

            def on_trace(step: Any) -> None:
                self._send_stream_event("trace", _jsonable(step))

            assert self.container is not None
            result = asyncio.run(self.container.runtime.run(request, on_trace=on_trace))
            self._send_stream_event("final", _jsonable(result))
        except ValueError as exc:
            if streaming:
                self._send_stream_event("error", {"error": str(exc)})
            else:
                self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            LOGGER.exception("local-agent stream request failed")
            if streaming:
                self._send_stream_event("error", {"error": str(exc)})
            else:
                self._send_json({"error": str(exc)}, status=500)

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
                vault_path = _text(payload, "vaultPath")
                vault_root = Path(vault_path).expanduser().resolve()
                if not vault_root.exists() or not vault_root.is_dir():
                    raise ValueError(f"vault path does not exist: {vault_root}")
                if not (vault_root / ".obsidian").is_dir():
                    raise ValueError("vault path must contain a .obsidian directory")
                if self.vault_root is not None and vault_root != self.vault_root:
                    raise ValueError("local-agent is already bound to another vault")

                if self.container is None:
                    settings = LocalAgentSettings(
                        host=self.settings.host,
                        port=self.settings.port,
                        vault_root=vault_root,
                        execution_mode=str(payload.get("executionMode") or self.settings.execution_mode),
                        **_intent_llm_settings(payload, self.settings),
                    )
                    self.__class__.container = build_container(settings)
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
            if not self._require_ready() or not self._require_token():
                return
            payload = self._read_json()
            mode = _text(payload, "executionMode")
            assert self.container is not None
            if "intentLlm" in payload and self.vault_root is not None:
                self.__class__.container = build_container(
                    LocalAgentSettings(
                        host=self.settings.host,
                        port=self.settings.port,
                        vault_root=self.vault_root,
                        execution_mode=mode,
                        **_intent_llm_settings(payload, self.settings),
                    )
                )
            else:
                self.container.operations.set_mode(mode)
            assert self.container is not None
            self._send_json({"executionMode": self.container.operations.policy.mode.value})
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=400)

    def _stage_operation(self) -> None:
        try:
            if not self._require_ready() or not self._require_token():
                return
            payload = self._read_json()
            assert self.container is not None
            plan = asyncio.run(self.container.operations.stage(payload))
            self._send_json(_jsonable(plan), status=201)
        except Exception as exc:
            LOGGER.exception("operation staging failed")
            self._send_json({"error": str(exc)}, status=400)

    def _execute_operation(self, path: str) -> None:
        try:
            if not self._require_ready() or not self._require_token():
                return
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
        except Exception as exc:
            LOGGER.exception("operation execution failed")
            self._send_json({"error": str(exc)}, status=500)

    def _rollback_operation(self, path: str) -> None:
        try:
            if not self._require_ready() or not self._require_token():
                return
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
        except Exception as exc:
            LOGGER.exception("operation rollback failed")
            self._send_json({"error": str(exc)}, status=500)

    def _require_ready(self) -> bool:
        if self.container is not None:
            return True
        self._send_json({"error": "local-agent is waiting for handshake"}, status=409)
        return False

    def _require_token(self) -> bool:
        expected = self.token
        if not expected:
            self._send_json({"error": "local-agent token is not configured"}, status=403)
            return False
        actual = self.headers.get("X-Agent-Token", "")
        if secrets.compare_digest(actual, expected):
            return True
        self._send_json({"error": "invalid local-agent token"}, status=403)
        return False

    def _read_json(self) -> dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0"))
        if size < 0 or size > MAX_REQUEST_BYTES:
            raise ValueError("request body is too large")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json")
        raw = self.rfile.read(size).decode("utf-8")
        value = json.loads(raw or "{}")
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _send_json(self, value: Any, *, status: int = 200) -> None:
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
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
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

    def _send_stream_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin") or "*")
        self.send_header("Vary", "Origin")

    def _send_stream_event(self, event: str, value: Any) -> None:
        raw = (
            f"event: {event}\n"
            f"data: {json.dumps(value, ensure_ascii=False)}\n\n"
        ).encode("utf-8")
        self.wfile.write(raw)
        self.wfile.flush()

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info(format, *args)


def main() -> None:
    """Start the local Agent service."""
    configure_logging()
    settings = load_settings()
    if settings.host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("local-agent must bind to localhost")

    LocalAgentHandler.settings = settings
    if settings.vault_root is not None:
        if not settings.vault_root.exists():
            raise RuntimeError(f"vault root does not exist: {settings.vault_root}")
        LocalAgentHandler.container = build_container(settings)
        LocalAgentHandler.vault_root = settings.vault_root
        LocalAgentHandler.token = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer((settings.host, settings.port), LocalAgentHandler)
    LOGGER.info("local-agent listening on http://%s:%s", settings.host, settings.port)
    LOGGER.info("vault root: %s", settings.vault_root or "waiting for handshake")
    server.serve_forever()


def _text(payload: dict[str, Any], *keys: str) -> str:
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


def _chat_request(payload: dict[str, Any]) -> RuntimeRequest:
    trigger = RuntimeTrigger(str(payload.get("trigger") or RuntimeTrigger.USER_MESSAGE.value))
    if trigger is not RuntimeTrigger.USER_MESSAGE:
        raise ValueError("system operation triggers must use the authenticated /operations endpoints")
    return RuntimeRequest(
        user_input=_text(payload, "userInput", "question", "message"),
        conversation_id=str(payload.get("conversationId") or "default"),
        scope=str(payload.get("scope") or "vault"),
        trigger=trigger,
        operation_plan_id=_optional_text(payload.get("operationPlanId")),
        active_file_path=_optional_text(payload.get("activeFilePath")),
        selected_text=_optional_text(payload.get("selectedText")),
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )


def _intent_llm_settings(payload: dict[str, Any], fallback: LocalAgentSettings) -> dict[str, str | None]:
    config = payload.get("intentLlm")
    if not isinstance(config, dict):
        return {
            "intent_llm_base_url": fallback.intent_llm_base_url,
            "intent_llm_model": fallback.intent_llm_model,
            "intent_llm_api_key": fallback.intent_llm_api_key,
        }
    return {
        "intent_llm_base_url": _optional_text(config.get("baseUrl")) or fallback.intent_llm_base_url,
        "intent_llm_model": _optional_text(config.get("model")) or fallback.intent_llm_model,
        "intent_llm_api_key": _optional_text(config.get("apiKey")) or fallback.intent_llm_api_key,
    }


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


if __name__ == "__main__":
    main()
