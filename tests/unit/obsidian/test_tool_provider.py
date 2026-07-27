from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping, Sequence

from agent.permissions import ToolAccess
from agent.protocol.mode import ModeKind
from agent.tools.invocation import ToolInvocation
from agent.tools.registry import ToolRegistry
from agent.tools.types import ToolExecutionContext
from agent.obsidian.tool_provider import ObsidianToolDependencies, build_obsidian_tools


class FakeNotes:
    async def get(self, path: str) -> Mapping[str, Any]:
        return {"path": path, "title": path.removesuffix(".md"), "content": "hello"}

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        filters: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        return ({"path": "Found.md", "title": "Found", "query": query},)

    async def all(self, *, limit: int = 5000) -> Sequence[Mapping[str, Any]]:
        return (await self.get("Current.md"),)

    async def rules(self) -> Sequence[Mapping[str, Any]]:
        return ()


class FakeTasks:
    async def list(self, *args: Any, **kwargs: Any) -> Sequence[Mapping[str, Any]]:
        return ()


class FakePlanner:
    context: Mapping[str, Any] | None = None

    async def build(
        self,
        requested_operations: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self.context = dict(context)
        return {
            "id": "op_1",
            "type": "operation-plan",
            "summary": "preview",
            "risk": "low",
            "operations": tuple(requested_operations),
            "context": dict(context),
        }


class FakePlanStore:
    async def save(self, plan: Mapping[str, Any]) -> Mapping[str, Any]:
        return plan


async def _run() -> None:
    planner = FakePlanner()
    tools = build_obsidian_tools(
        ObsidianToolDependencies(
            notes=FakeNotes(),
            tasks=FakeTasks(),
            operation_planner=planner,
            operation_plan_store=FakePlanStore(),
            operation_executor=object(),
        )
    )
    names = {tool.spec.name for tool in tools}
    assert {"search_notes", "read_note", "list_tasks", "build_operation_plan"} <= names
    assert not {"apply_patch", "exec_command", "write_stdin", "execute_operation_plan", "rollback_operation"} & names

    registry = ToolRegistry(list(tools))
    context = ToolExecutionContext(
        session_id="session-1",
        submission_id=1,
        mode=ModeKind.DEFAULT,
        metadata={"activeFilePath": "Current.md", "referencedPaths": ["Referenced.md"]},
    )
    searched = await registry.dispatch(
        ToolInvocation("call-1", "search_notes", {"query": "found"}),
        granted_access=ToolAccess.READ_ONLY,
        context=context,
    )
    assert json.loads(searched.content)["results"][0]["path"] == "Found.md"

    current_context = ToolExecutionContext(
        session_id="session-2",
        submission_id=1,
        mode=ModeKind.DEFAULT,
        metadata={"scope": "current", "activeFilePath": "Current.md"},
    )
    current_search = await registry.dispatch(
        ToolInvocation("call-current", "search_notes", {"query": "found", "scope": "vault"}),
        context=current_context,
    )
    assert json.loads(current_search.content)["results"][0]["path"] == "Current.md"
    denied_read = await registry.dispatch(
        ToolInvocation("call-denied", "read_note", {"path": "Other.md"}),
        context=current_context,
    )
    assert denied_read.is_error
    assert "current-note scope" in denied_read.content

    preview = await registry.dispatch(
        ToolInvocation(
            "call-2",
            "build_operation_plan",
            {"requestedOperations": [{"type": "update-note", "path": "Found.md", "content": "new"}]},
        ),
        granted_access=ToolAccess.WORKSPACE_WRITE,
        context=context,
    )
    assert json.loads(preview.content)["operationPlanId"] == "op_1"
    assert planner.context is not None
    assert planner.context["allowedPaths"] == ["Current.md", "Found.md", "Referenced.md"]

    denied = await registry.dispatch(
        ToolInvocation(
            "call-3",
            "build_operation_plan",
            {"requestedOperations": [{"type": "update-note", "path": "Current.md", "content": "new"}]},
        ),
        granted_access=ToolAccess.WORKSPACE_WRITE,
        mode=ModeKind.PLAN,
        context=ToolExecutionContext("session-1", 2, ModeKind.PLAN, {}),
    )
    assert denied.is_error
    assert "Plan Mode" in denied.content


def test_obsidian_tools() -> None:
    asyncio.run(_run())
