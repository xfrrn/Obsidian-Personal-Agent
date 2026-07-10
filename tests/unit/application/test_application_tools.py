from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "packages" / "agent-core"))

from application.tool_provider import ApplicationToolDependencies, build_application_tools  # noqa: E402
from tools import ToolCall, ToolPermission, ToolPolicy, ToolRegistry, ToolRiskLevel  # noqa: E402


class FakeNotes:
    async def get(self, path: str) -> Mapping[str, Any]:
        return {"path": path, "title": "Today", "content": "hello"}

    async def search(self, query: str, *, limit: int = 10) -> Sequence[Mapping[str, Any]]:
        return ({"path": "Today.md", "score": 1, "query": query, "limit": limit},)


class FakeTasks:
    async def list(
        self,
        query: str = "",
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> Sequence[Mapping[str, Any]]:
        return ({"title": "write tests", "status": status or "open", "query": query, "limit": limit},)


class FakePlanner:
    async def build(
        self,
        requested_operations: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return {
            "id": "op_1",
            "schemaVersion": "1.0",
            "type": "operation-plan",
            "summary": "test plan",
            "risk": "low",
            "operations": tuple(requested_operations),
            "context": dict(context),
        }


class FakePlanStore:
    def __init__(self) -> None:
        self.plan: Mapping[str, Any] | None = None

    async def save(self, plan: Mapping[str, Any]) -> Mapping[str, Any]:
        self.plan = dict(plan)
        return self.plan

    async def get(self, operation_plan_id: str) -> Mapping[str, Any]:
        assert operation_plan_id == "op_1"
        assert self.plan is not None
        return self.plan


class FakeExecutor:
    async def execute(self, plan: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        return ({"status": "ok", "planId": plan["id"]},)


async def _run() -> None:
    store = FakePlanStore()
    registry = ToolRegistry(
        ToolPolicy.allow({ToolPermission.READ, ToolPermission.WRITE}, ToolRiskLevel.HIGH)
    )
    registry.extend(
        build_application_tools(
            ApplicationToolDependencies(
                notes=FakeNotes(),
                tasks=FakeTasks(),
                operation_planner=FakePlanner(),
                operation_plan_store=store,
                operation_executor=FakeExecutor(),
            )
        )
    )

    assert registry.names() == (
        "search_notes",
        "read_note",
        "list_tasks",
        "build_operation_plan",
        "execute_operation_plan",
    )

    search = await registry.run(ToolCall("search_notes", {"query": "agent", "limit": 1}))
    assert search.output["results"][0]["path"] == "Today.md"

    note = await registry.run(ToolCall("read_note", {"path": "Today.md"}))
    assert note.output["note"]["content"] == "hello"

    tasks = await registry.run(ToolCall("list_tasks", {"rawText": "tasks"}))
    assert tasks.output["count"] == 1

    plan = await registry.run(
        ToolCall(
            "build_operation_plan",
            {
                "requestedOperations": ({"type": "create-task", "title": "ship"},),
                "context": {"activeFilePath": "Today.md"},
            },
        )
    )
    assert plan.output["operationPlanId"] == "op_1"

    executed = await registry.run(
        ToolCall("execute_operation_plan", {"operationPlanId": "op_1"}),
        confirmed=True,
    )
    assert executed.output["results"][0]["status"] == "ok"


if __name__ == "__main__":
    asyncio.run(_run())
