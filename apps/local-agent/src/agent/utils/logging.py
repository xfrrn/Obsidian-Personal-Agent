"""Agent runtime's small, safe diagnostic logging boundary."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import os
import sys
from typing import Any, TextIO


_LOGGER_NAME = "agent"
_CONTEXT_FIELDS = ("submission_id", "tool_name", "tool_call_id")
_RECORD_FIELDS = (
    *_CONTEXT_FIELDS,
    "round",
    "duration_ms",
    "streamed",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "is_error",
    "error_type",
    "summary_bytes",
    "history_message_count",
    "delta_chars",
    "tool_status",
    "skill_name",
    "http_method",
    "http_path",
    "provider",
    "operation",
    "query_hash",
    "query_chars",
    "result_count",
    "source_count",
    "source_ids",
    "http_status",
    "retry_count",
    "key_index",
    "key_count",
    "cache_hit",
)
_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
_FORMATS = {"text", "json"}
_HANDLER_MARKER = "_agent_logging_handler"
_context: ContextVar[dict[str, str | int]] = ContextVar("agent_log_context", default={})
_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_BRIGHT = "\033[97m"
_LEVEL_COLORS = {
    "DEBUG": "\033[36m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[1;31m",
}
_EVENT_COLORS = {
    "turn": "\033[34m",
    "llm": "\033[35m",
    "tool": "\033[36m",
    "context": "\033[33m",
    "http": "\033[34m",
    "audit": "\033[36m",
}


def configure_logging(level: str, log_format: str, *, stream: TextIO | None = None) -> None:
    """Configure only the project's logger, leaving an embedding host's root logger untouched."""

    normalized_level = normalize_log_level(level)
    normalized_format = normalize_log_format(log_format)
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(normalized_level)
    logger.propagate = False

    handler = _owned_handler(logger)
    if handler is None:
        handler = logging.StreamHandler()
        setattr(handler, _HANDLER_MARKER, True)
        handler.addFilter(_ContextFilter())
        logger.addHandler(handler)

    # Entry points can be created repeatedly in tests or embedded programs; reuse the
    # owned handler so every event is emitted once rather than accumulating handlers.
    output = sys.stderr if stream is None else stream
    handler.setStream(output)
    handler.setFormatter(
        _JsonFormatter()
        if normalized_format == "json"
        else _TextFormatter(use_color=_should_use_color(output))
    )


@contextmanager
def log_context(**values: str | int) -> Iterator[None]:
    """Bind safe correlation fields for the current async task and its child tasks."""

    unknown = set(values) - set(_CONTEXT_FIELDS)
    if unknown:
        raise ValueError(f"Unsupported log context fields: {', '.join(sorted(unknown))}")
    token = _context.set({**_context.get(), **values})
    try:
        yield
    finally:
        _context.reset(token)


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in _context.get().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


class _TextFormatter(logging.Formatter):
    def __init__(self, *, use_color: bool) -> None:
        super().__init__()
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        timestamp = _timestamp(record)
        event = record.getMessage()
        if self._use_color:
            result = " ".join(
                (
                    _paint(timestamp, _DIM),
                    _paint(f"{record.levelname:<8}", _LEVEL_COLORS[record.levelname] + _BOLD),
                    _paint(record.name, _DIM),
                    _paint(event, _EVENT_COLORS.get(event.partition(".")[0], _BRIGHT)),
                )
            )
        else:
            result = f"{timestamp} {record.levelname:<8} {record.name} {event}"
        fields = " ".join(
            _format_field(name, value, self._use_color)
            for name in _RECORD_FIELDS
            if (value := getattr(record, name, None)) is not None
        )
        return f"{result} {fields}" if fields else result


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _timestamp(record),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "process": record.process,
        }
        for name in _RECORD_FIELDS:
            if (value := getattr(record, name, None)) is not None:
                payload[name] = value
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _owned_handler(logger: logging.Logger) -> logging.StreamHandler[TextIO] | None:
    for handler in logger.handlers:
        if getattr(handler, _HANDLER_MARKER, False) and isinstance(handler, logging.StreamHandler):
            return handler
    return None


def normalize_log_level(level: str) -> str:
    """Validate the shared environment setting without configuring any handlers."""

    normalized = level.strip().upper()
    if normalized not in _LEVELS:
        raise ValueError("AGENT_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    return normalized


def normalize_log_format(log_format: str) -> str:
    """Validate the shared environment setting without configuring any handlers."""

    normalized = log_format.strip().lower()
    if normalized not in _FORMATS:
        raise ValueError("AGENT_LOG_FORMAT must be text or json")
    return normalized


def _timestamp(record: logging.LogRecord) -> str:
    return datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _should_use_color(stream: TextIO) -> bool:
    """Avoid escape sequences in redirected logs while respecting the common NO_COLOR flag."""

    return not os.getenv("NO_COLOR") and stream.isatty()


def _format_field(name: str, value: Any, use_color: bool) -> str:
    rendered = _text_value(value)
    if not use_color:
        return f"{name}={rendered}"
    return f"{_paint(name, _DIM)}={_paint(rendered, _BRIGHT)}"


def _paint(text: str, color: str) -> str:
    return f"{color}{text}{_RESET}"


def _text_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False) if isinstance(value, str) else str(value)
