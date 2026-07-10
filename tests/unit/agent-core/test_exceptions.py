from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "agent-core"))

from exceptions import (  # noqa: E402
    AgentCoreError,
    AgentValidationError,
    ToolNotFoundError,
    ToolPermissionDeniedError,
)
from tools import ToolCall, ToolDefinition, ToolPermission, ToolRegistry  # noqa: E402


def test_agent_core_error_can_be_serialized() -> None:
    error = AgentCoreError("失败", code="x", details={"a": 1})
    assert error.to_dict() == {
        "code": "x",
        "message": "失败",
        "details": {"a": 1},
    }


def test_validation_error_keeps_value_error_semantics() -> None:
    try:
        ToolDefinition("Bad Name", "bad")
    except ValueError as error:
        assert isinstance(error, AgentValidationError)
    else:
        raise AssertionError("invalid tool name should fail")


def test_registry_errors_keep_builtin_semantics() -> None:
    asyncio.run(_test_registry_errors_keep_builtin_semantics())


async def _test_registry_errors_keep_builtin_semantics() -> None:
    registry = ToolRegistry()
    try:
        registry.get("missing")
    except KeyError as error:
        assert isinstance(error, ToolNotFoundError)
    else:
        raise AssertionError("missing tool should fail")

    registry.register_function(
        ToolDefinition("write_note", "写笔记", permission=ToolPermission.WRITE),
        lambda _input, _context: None,
    )
    try:
        await registry.run(ToolCall("write_note"))
    except PermissionError as error:
        assert isinstance(error, ToolPermissionDeniedError)
    else:
        raise AssertionError("write tool should fail under read-only policy")


if __name__ == "__main__":
    test_agent_core_error_can_be_serialized()
    test_validation_error_keeps_value_error_semantics()
    test_registry_errors_keep_builtin_semantics()
