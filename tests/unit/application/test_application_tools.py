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
        return {
            "path": path,
            "title": "Today",
            "content": "hello\n下一步：写测试",
            "headings": (),
            "metadata": {"project": "Agent", "status": "active"},
            "tags": ("agent",),
            "links": (),
            "project": "Agent",
            "modifiedAt": "2026-07-12T00:00:00+08:00",
            "frontmatterError": None,
        }

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        filters: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        return ({"path": "Today.md", "title": "Today", "score": 1, "query": query, "limit": limit, "filters": filters},)

    async def all(self, *, limit: int = 5000) -> Sequence[Mapping[str, Any]]:
        return (await self.get("Today.md"),)

    async def rules(self) -> Sequence[Mapping[str, Any]]:
        return ()


class FakeTasks:
    async def list(
        self,
        query: str = "",
        *,
        status: str | None = None,
        limit: int = 50,
        path: str | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        return ({
            "title": "write tests",
            "rawTitle": "write tests 📅 2026-07-12",
            "lineText": "- [ ] write tests 📅 2026-07-12",
            "completed": status == "completed",
            "status": status or "open",
            "query": query,
            "limit": limit,
            "path": path or "Today.md",
            "filters": filters,
        },)


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

    async def rollback(self, plan: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        return ({"status": "rolled_back", "planId": plan["id"]},)


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
        "inspect_note",
        "analyze_project",
        "check_vault_health",
        "list_tags",
        "find_related_notes",
        "find_duplicates",
        "list_rules",
        "evaluate_rules",
        "extract_task_candidates",
        "build_operation_plan",
        "execute_operation_plan",
        "rollback_operation",
    )

    search = await registry.run(ToolCall("search_notes", {"query": "agent", "limit": 1}))
    assert search.output["results"][0]["path"] == "Today.md"

    note = await registry.run(ToolCall("read_note", {"path": "Today.md"}))
    assert note.output["note"]["content"].startswith("hello")
    batch = await registry.run(ToolCall("read_note", {"paths": ["Today.md", "Other.md"]}))
    assert batch.output["count"] == 2

    tasks = await registry.run(ToolCall("list_tasks", {"rawText": "tasks"}))
    assert tasks.output["count"] == 1
    assert tasks.output["query"] == ""
    completed = await registry.run(ToolCall("list_tasks", {"rawText": "已完成任务"}))
    assert completed.output["status"] == "completed"

    current = await registry.run(ToolCall("search_notes", {"scope": "current", "activeFilePath": "Today.md"}))
    assert current.output["results"][0]["path"] == "Today.md"
    current_tasks = await registry.run(ToolCall("list_tasks", {"scope": "current", "activeFilePath": "Today.md"}))
    assert current_tasks.output["tasks"][0]["path"] == "Today.md"

    inspection = await registry.run(ToolCall("inspect_note", {"path": "Today.md"}))
    assert inspection.output["classification"] == "note"
    project = await registry.run(ToolCall("analyze_project", {"project": "Agent"}))
    assert project.output["noteCount"] == 1
    health = await registry.run(ToolCall("check_vault_health"))
    assert health.output["noteCount"] == 1
    tags = await registry.run(ToolCall("list_tags"))
    assert tags.output["tags"][0]["tag"] == "agent"
    candidates = await registry.run(ToolCall("extract_task_candidates", {"path": "Today.md"}))
    assert candidates.output["count"] == 1

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

    complete_plan = await registry.run(
        ToolCall(
            "build_operation_plan",
            {
                "requestedOperations": ({"intent": "task.complete", "arguments": {"rawText": "把今天需要完成的任务标记为完成"}},),
                "context": {"activeFilePath": "Today.md"},
            },
        )
    )
    complete_operation = complete_plan.output["plan"]["operations"][0]
    assert complete_operation["type"] == "update-note"
    assert complete_operation["oldText"] == "- [ ] write tests 📅 2026-07-12"
    assert complete_operation["newText"] == "- [x] write tests 📅 2026-07-12"

    try:
        await registry.run(
            ToolCall(
                "build_operation_plan",
                {
                    "requestedOperations": ({"intent": "task.complete", "arguments": {"rawText": "完成任务"}},),
                    "context": {"activeFilePath": "Today.md"},
                },
            )
        )
    except ValueError as error:
        assert "请说明要完成哪个任务" in str(error)
    else:
        raise AssertionError("ambiguous task completion must not mark every open task")

    executed = await registry.run(
        ToolCall("execute_operation_plan", {"operationPlanId": "op_1"}),
        confirmed=True,
    )
    assert executed.output["results"][0]["status"] == "ok"

    rolled_back = await registry.run(
        ToolCall("rollback_operation", {"operationPlanId": "op_1"}),
        confirmed=True,
    )
    assert rolled_back.output["results"][0]["status"] == "rolled_back"


def test_application_tools() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    test_application_tools()
