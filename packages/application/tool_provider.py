"""Application 用例注册为 Agent Core 工具。"""

from __future__ import annotations

from dataclasses import dataclass

from .notes.read_note import ReadNoteUseCase
from .operations.build_operation_plan import BuildOperationPlanUseCase
from .operations.execute_operation_plan import ExecuteOperationPlanUseCase
from .operations.rollback_operation import RollbackOperationUseCase
from .search.search_notes import SearchNotesUseCase
from .tasks.list_tasks import ListTasksUseCase
from tools import (
    FunctionTool,
    Tool,
    ToolDefinition,
    ToolEffect,
    ToolInvocationPolicy,
    ToolPermission,
    ToolRiskLevel,
)


@dataclass(frozen=True)
class ApplicationToolDependencies:
    notes: object
    tasks: object
    operation_planner: object
    operation_plan_store: object
    operation_executor: object


def build_application_tools(deps: ApplicationToolDependencies) -> tuple[Tool, ...]:
    """返回第一批由 Application 用例驱动的 Agent 工具。"""
    search_notes = SearchNotesUseCase(deps.notes)
    read_note = ReadNoteUseCase(deps.notes)
    list_tasks = ListTasksUseCase(deps.tasks)
    build_plan = BuildOperationPlanUseCase(deps.operation_planner, deps.operation_plan_store)
    execute_plan = ExecuteOperationPlanUseCase(deps.operation_plan_store, deps.operation_executor)
    rollback_plan = RollbackOperationUseCase(deps.operation_plan_store, deps.operation_executor)

    return (
        FunctionTool(
            ToolDefinition(
                name="search_notes",
                description="在知识库中搜索相关笔记",
                input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            ),
            lambda input_data, _context: search_notes.execute(input_data),
        ),
        FunctionTool(
            ToolDefinition(
                name="read_note",
                description="按知识库相对路径读取一篇笔记",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            ),
            lambda input_data, _context: read_note.execute(input_data),
        ),
        FunctionTool(
            ToolDefinition(
                name="list_tasks",
                description="列出笔记中的任务和待办事项",
                input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            ),
            lambda input_data, _context: list_tasks.execute(input_data),
        ),
        FunctionTool(
            ToolDefinition(
                name="build_operation_plan",
                description="为写入请求生成安全的操作计划",
                permission=ToolPermission.WRITE,
                risk_level=ToolRiskLevel.LOW,
                effect=ToolEffect.PREPARE_WRITE,
            ),
            lambda input_data, _context: build_plan.execute(input_data),
        ),
        FunctionTool(
            ToolDefinition(
                name="execute_operation_plan",
                description="执行已经确认的操作计划",
                permission=ToolPermission.WRITE,
                risk_level=ToolRiskLevel.HIGH,
                effect=ToolEffect.WRITE,
                invocation_policy=ToolInvocationPolicy.SYSTEM_ONLY,
                requires_confirmation=True,
            ),
            lambda input_data, _context: execute_plan.execute(input_data),
        ),
        FunctionTool(
            ToolDefinition(
                name="rollback_operation",
                description="撤销已经成功执行且当前版本未冲突的操作计划",
                input_schema={
                    "type": "object",
                    "properties": {"operationPlanId": {"type": "string"}},
                    "required": ["operationPlanId"],
                },
                permission=ToolPermission.WRITE,
                risk_level=ToolRiskLevel.HIGH,
                effect=ToolEffect.WRITE,
                invocation_policy=ToolInvocationPolicy.SYSTEM_ONLY,
                requires_confirmation=True,
            ),
            lambda input_data, _context: rollback_plan.execute(input_data),
        ),
    )
