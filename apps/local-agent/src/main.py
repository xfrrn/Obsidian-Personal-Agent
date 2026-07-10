"""Local Agent service entrypoint."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from bootstrap.container import LocalAgentContainer, build_container
from bootstrap.settings import load_settings
from infrastructure.logging import configure_logging
from runtime import RuntimeRequest, RuntimeTrigger

LOGGER = logging.getLogger(__name__)


class LocalAgentHandler(BaseHTTPRequestHandler):
    container: LocalAgentContainer

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json({"status": "ok"})
            return
        if self.path == "/tools":
            self._send_json({"tools": [_jsonable(tool) for tool in self.container.registry.definitions()]})
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        if self.path != "/chat":
            self._send_json({"error": "not found"}, status=404)
            return
        try:
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
            result = asyncio.run(self.container.runtime.run(request))
            self._send_json(_jsonable(result))
        except Exception as exc:
            LOGGER.exception("local-agent request failed")
            self._send_json({"error": str(exc)}, status=500)

    def _read_json(self) -> dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(size).decode("utf-8")
        value = json.loads(raw or "{}")
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _send_json(self, value: Any, *, status: int = 200) -> None:
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._send_cors_headers()
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info(format, *args)


def main() -> None:
    """Start the local Agent service."""
    configure_logging()
    settings = load_settings()
    if not settings.vault_root.exists():
        raise RuntimeError(f"vault root does not exist: {settings.vault_root}")

    LocalAgentHandler.container = build_container(settings)
    server = ThreadingHTTPServer((settings.host, settings.port), LocalAgentHandler)
    LOGGER.info("local-agent listening on http://%s:%s", settings.host, settings.port)
    LOGGER.info("vault root: %s", settings.vault_root)
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
