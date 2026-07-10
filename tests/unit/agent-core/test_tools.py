from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "agent-core"))

from tools import (  # noqa: E402
    ToolCall,
    ToolDefinition,
    ToolPermission,
    ToolInvocationPolicy,
    ToolPolicy,
    ToolRegistry,
    ToolRiskLevel,
)


async def _run() -> None:
    registry = ToolRegistry(ToolPolicy.allow({ToolPermission.READ}))
    definition = ToolDefinition(
        name="list_tasks",
        description="List Markdown tasks",
        permission=ToolPermission.READ,
        risk_level=ToolRiskLevel.LOW,
    )
    registry.register_function(definition, lambda input_data, _context: {"count": input_data["count"]})

    result = await registry.run(ToolCall("list_tasks", {"count": 2}))
    assert result.output == {"count": 2}
    assert registry.names() == ("list_tasks",)
    assert registry.definitions() == (definition,)

    try:
        registry.register_function(definition, lambda _input, _context: None)
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate tool registration should fail")

    write_registry = ToolRegistry(ToolPolicy.allow({ToolPermission.READ}))
    write_registry.register_function(
        ToolDefinition(
            name="execute_operation_plan",
            description="Execute a confirmed operation plan",
            permission=ToolPermission.WRITE,
            risk_level=ToolRiskLevel.HIGH,
        ),
        lambda _input, _context: "done",
    )
    try:
        await write_registry.run(ToolCall("execute_operation_plan"), confirmed=True)
    except PermissionError:
        pass
    else:
        raise AssertionError("write tool should be denied by read-only policy")

    confirmed_registry = ToolRegistry(
        ToolPolicy.allow({ToolPermission.WRITE}, ToolRiskLevel.HIGH)
    )
    confirmed_registry.register_function(
        ToolDefinition(
            name="confirmed_write",
            description="Confirmed write",
            permission=ToolPermission.WRITE,
            risk_level=ToolRiskLevel.HIGH,
            invocation_policy=ToolInvocationPolicy.SYSTEM_ONLY,
            requires_confirmation=True,
        ),
        lambda _input, _context: "done",
    )
    try:
        await confirmed_registry.run(ToolCall("confirmed_write"))
    except PermissionError:
        pass
    else:
        raise AssertionError("system-only tool should require confirmation")
    assert (await confirmed_registry.run(ToolCall("confirmed_write"), confirmed=True)).output == "done"


def test_tool_registry() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    test_tool_registry()
