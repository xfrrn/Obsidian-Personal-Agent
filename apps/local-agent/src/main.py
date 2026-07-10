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
        self._send_json({"error": "browser access is not allowed"}, status=403)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json({
                "status": "ok",
                "configured": self.container is not None,
            })
            return
        if self.path == "/tools":
            if not self._require_ready() or not self._require_token():
                return
            self._send_json({"tools": [_jsonable(tool) for tool in self.container.registry.definitions()]})
            return
        if self.path == "/identity":
            if not self._require_ready() or not self._require_token():
                return
            self._send_json({"vaultRoot": str(self.vault_root)})
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        if self.path == "/handshake":
            self._handshake()
            return
        if self.path != "/chat":
            self._send_json({"error": "not found"}, status=404)
            return
        try:
            if not self._require_ready() or not self._require_token():
                return
            payload = self._read_json()
            request = RuntimeRequest(
                user_input=_text(payload, "userInput", "question", "message"),
                conversation_id=str(payload.get("conversationId") or "default"),
                scope=str(payload.get("scope") or "vault"),
                trigger=RuntimeTrigger(str(payload.get("trigger") or RuntimeTrigger.USER_MESSAGE.value)),
                operation_plan_id=_optional_text(payload.get("operationPlanId")),
                active_file_path=_optional_text(payload.get("activeFilePath")),
                selected_text=_optional_text(payload.get("selectedText")),
                metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
            )
            assert self.container is not None
            result = asyncio.run(self.container.runtime.run(request))
            self._send_json(_jsonable(result))
        except Exception as exc:
            LOGGER.exception("local-agent request failed")
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
