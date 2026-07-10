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


if __name__ == "__main__":
    asyncio.run(_run())
