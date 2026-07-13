"""Build OperationPlan use case."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from domain.operations.repositories import OperationPlanStore, OperationPlanner
from domain.tasks.repositories import TaskRepository
from application.tasks.list_tasks import _context_tasks, _date_filters, _filter_context_tasks


@dataclass(frozen=True)
class BuildOperationPlanUseCase:
    planner: OperationPlanner
    store: OperationPlanStore
    tasks: TaskRepository | None = None

    async def execute(self, input_data: Mapping[str, Any]) -> dict[str, Any]:
        requested = input_data.get("requestedOperations", ())
        if not isinstance(requested, (list, tuple)):
            raise ValueError("requestedOperations must be a list")
        context = input_data.get("context", {})
        if not isinstance(context, Mapping):
            raise ValueError("context must be an object")

        operations = await self._expand_operations(tuple(requested), context)
        plan = await self.planner.build(operations, context)
        stored = await self.store.save(plan)
        plan_id = stored.get("id") or stored.get("operationPlanId")
        return {
            "message": "已生成操作计划，等待确认。",
            "operationPlanId": plan_id,
            "plan": stored,
        }

    async def _expand_operations(
        self,
        requested: tuple[Mapping[str, Any], ...],
        context: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], ...]:
        operations: list[Mapping[str, Any]] = []
        for operation in requested:
            if operation.get("intent") == "task.complete":
                operations.extend(await self._complete_task_operations(operation, context))
            else:
                operations.append(operation)
        return tuple(operations)

    async def _complete_task_operations(
        self,
        operation: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], ...]:
        if self.tasks is None:
            raise ValueError("task completion needs a task repository")
        arguments = operation.get("arguments") if isinstance(operation.get("arguments"), Mapping) else {}
        raw_text = str(arguments.get("rawText") or "")
        filters = _date_filters(raw_text)
        if project := _text(arguments.get("projectName")):
            filters["project"] = project
        path = _text(arguments.get("activeFilePath")) if arguments.get("scope") == "current" else ""
        query = "" if filters else _text(arguments.get("taskName"))
        if not (query or filters or path):
            raise ValueError("请说明要完成哪个任务，或提供日期、项目、当前笔记范围")
        context_tasks = _context_tasks(context.get("contextTasks"))
        if context_tasks is None:
            tasks = tuple(await self.tasks.list(
                query,
                status="open",
                limit=11,
                path=path or None,
                filters=filters,
            ))
        else:
            tasks = _filter_context_tasks(context_tasks, query, "open", 11, path or None, filters)
        if not tasks:
            raise ValueError("没有找到需要标记完成的任务")
        if len(tasks) > 10:
            raise ValueError("一次最多标记 10 个任务，请缩小筛选范围")
        return tuple(_complete_task_operation(task) for task in tasks)


def _complete_task_operation(task: Mapping[str, Any]) -> Mapping[str, Any]:
    old_text = _text(task.get("lineText")) or f"- [ ] {_text(task.get('rawTitle')) or _text(task.get('title'))}"
    new_text = _text(task.get("completedLineText"))
    if not new_text and "[ ]" not in old_text:
        raise ValueError("task line is not an open Markdown task")
    return {
        "type": "update-note",
        "path": _text(task.get("path")),
        "oldText": old_text,
        "newText": new_text or old_text.replace("[ ]", "[x]", 1),
    }


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
