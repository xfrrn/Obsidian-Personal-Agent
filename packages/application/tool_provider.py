"""Application 用例注册为 Agent Core 工具。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .notes.read_note import ReadNoteUseCase
from .notes.analyze_notes import (
    CheckVaultHealthUseCase,
    EvaluateRulesUseCase,
    ExtractTaskCandidatesUseCase,
    FindDuplicatesUseCase,
    FindRelatedNotesUseCase,
    InspectNoteUseCase,
    ListRulesUseCase,
    ListTagsUseCase,
)
from .operations.build_operation_plan import BuildOperationPlanUseCase
from .operations.execute_operation_plan import ExecuteOperationPlanUseCase
from .operations.rollback_operation import RollbackOperationUseCase
from .search.search_notes import SearchNotesUseCase
from .tasks.list_tasks import ListTasksUseCase
from .projects.analyze_project import AnalyzeProjectUseCase
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
    build_plan = BuildOperationPlanUseCase(deps.operation_planner, deps.operation_plan_store, deps.tasks)
    execute_plan = ExecuteOperationPlanUseCase(deps.operation_plan_store, deps.operation_executor)
    rollback_plan = RollbackOperationUseCase(deps.operation_plan_store, deps.operation_executor)
    inspect_note = InspectNoteUseCase(deps.notes)
    analyze_project = AnalyzeProjectUseCase(deps.notes, deps.tasks)
    check_health = CheckVaultHealthUseCase(deps.notes)
    list_tags = ListTagsUseCase(deps.notes)
    find_related = FindRelatedNotesUseCase(deps.notes)
    find_duplicates = FindDuplicatesUseCase(deps.notes)
    list_rules = ListRulesUseCase(deps.notes)
    evaluate_rules = EvaluateRulesUseCase(deps.notes)
    extract_tasks = ExtractTaskCandidatesUseCase(deps.notes)

    return (
        FunctionTool(
            ToolDefinition(
                name="search_notes",
                description="在知识库中搜索相关笔记",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "path_prefix": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "project": {"type": "string"},
                        "modified_after": {"type": "string"},
                        "modified_before": {"type": "string"},
                        "sort": {"enum": ["relevance", "modified_desc", "modified_asc", "path"]},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                },
            ),
            lambda input_data, _context: search_notes.execute(input_data),
        ),
        FunctionTool(
            ToolDefinition(
                name="read_note",
                description="按知识库相对路径读取一篇或一组受控笔记",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "paths": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
                    },
                },
            ),
            lambda input_data, _context: read_note.execute(input_data),
        ),
        FunctionTool(
            ToolDefinition(
                name="list_tasks",
                description="按状态、日期、优先级、项目和笔记范围列出 Tasks 待办",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "status": {"type": "string"},
                        "due_on": {"type": "string"},
                        "due_after": {"type": "string"},
                        "due_before": {"type": "string"},
                        "priority": {"type": "string"},
                        "project": {"type": "string"},
                        "path_prefix": {"type": "string"},
                        "rawText": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                    },
                },
            ),
            lambda input_data, context: list_tasks.execute(_with_turn_context(input_data, context)),
        ),
        FunctionTool(
            ToolDefinition(
                name="inspect_note",
                description="检查笔记规范、类型、链接、归档位置和缺失内容",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}, "activeFilePath": {"type": "string"}, "noteName": {"type": "string"}}},
            ),
            lambda input_data, _context: inspect_note.execute(input_data),
        ),
        FunctionTool(
            ToolDefinition(
                name="analyze_project",
                description="汇总项目笔记、任务、状态、缺失文档和长期未更新内容",
                input_schema={"type": "object", "properties": {"project": {"type": "string"}, "projectName": {"type": "string"}, "activeFilePath": {"type": "string"}}},
            ),
            lambda input_data, _context: analyze_project.execute(input_data),
        ),
        FunctionTool(
            ToolDefinition(
                name="check_vault_health",
                description="生成知识库规范和链接健康报告",
                input_schema={"type": "object", "properties": {"limit": {"type": "integer"}}},
            ),
            lambda input_data, _context: check_health.execute(input_data),
        ),
        FunctionTool(
            ToolDefinition(name="list_tags", description="统计知识库标签、分类、别名和废弃状态"),
            lambda input_data, _context: list_tags.execute(input_data),
        ),
        FunctionTool(
            ToolDefinition(
                name="find_related_notes",
                description="根据链接、标签、项目和词项重合查找相关笔记",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}, "activeFilePath": {"type": "string"}, "noteName": {"type": "string"}, "limit": {"type": "integer"}}},
            ),
            lambda input_data, _context: find_related.execute(input_data),
        ),
        FunctionTool(
            ToolDefinition(
                name="find_duplicates",
                description="查找正文完全重复或高度相似的笔记",
                input_schema={"type": "object", "properties": {"threshold": {"type": "number"}, "scan_limit": {"type": "integer"}, "limit": {"type": "integer"}}},
            ),
            lambda input_data, _context: find_duplicates.execute(input_data),
        ),
        FunctionTool(
            ToolDefinition(name="list_rules", description="列出当前知识库确定性管理规则"),
            lambda input_data, _context: list_rules.execute(input_data),
        ),
        FunctionTool(
            ToolDefinition(
                name="evaluate_rules",
                description="对指定笔记或目录试运行确定性规则但不写入",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}, "activeFilePath": {"type": "string"}, "path_prefix": {"type": "string"}}},
            ),
            lambda input_data, _context: evaluate_rules.execute(input_data),
        ),
        FunctionTool(
            ToolDefinition(
                name="extract_task_candidates",
                description="从笔记正文中提取尚未写成 Markdown Task 的潜在任务",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}, "activeFilePath": {"type": "string"}, "noteName": {"type": "string"}, "limit": {"type": "integer"}}},
            ),
            lambda input_data, _context: extract_tasks.execute(input_data),
        ),
        FunctionTool(
            ToolDefinition(
                name="build_operation_plan",
                description="为写入请求生成安全的操作计划",
                input_schema={
                    "type": "object",
                    "properties": {
                        "requestedOperations": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 10,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {
                                        "enum": [
                                            "create-note",
                                            "update-note",
                                            "move-note",
                                            "trash-note",
                                            "create-folder",
                                            "delete-folder",
                                            "update-metadata",
                                            "create-task",
                                        ]
                                    },
                                    "path": {"type": "string", "description": "Vault 内安全相对路径"},
                                    "content": {"type": "string"},
                                    "oldText": {"type": "string"},
                                    "newText": {"type": "string"},
                                    "targetPath": {"type": "string"},
                                    "title": {"type": "string"},
                                    "set": {"type": "object"},
                                    "remove": {"type": "array", "items": {"type": "string"}},
                                    "addTags": {"type": "array", "items": {"type": "string"}},
                                    "removeTags": {"type": "array", "items": {"type": "string"}},
                                    "intent": {"enum": ["task.complete"]},
                                    "arguments": {"type": "object"},
                                },
                                "anyOf": [
                                    {"required": ["type"]},
                                    {"required": ["intent", "arguments"]},
                                ],
                            },
                        },
                        "context": {
                            "type": "object",
                            "properties": {
                                "source": {"enum": ["interactive", "automation", "remote", "external"]},
                                "allowedPaths": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                    "required": ["requestedOperations"],
                },
                permission=ToolPermission.WRITE,
                risk_level=ToolRiskLevel.LOW,
                effect=ToolEffect.PREPARE_WRITE,
            ),
            lambda input_data, context: build_plan.execute(_with_operation_context(input_data, context)),
        ),
        FunctionTool(
            ToolDefinition(
                name="execute_operation_plan",
                description="执行已经确认的操作计划",
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


def _with_turn_context(input_data: Mapping[str, Any], context: Any) -> Mapping[str, Any]:
    value = dict(input_data)
    raw_text = getattr(context, "user_input", "")
    if "rawText" not in value and isinstance(raw_text, str) and raw_text.strip():
        value["rawText"] = raw_text
    scope = getattr(context, "scope", "")
    if "scope" not in value and isinstance(scope, str) and scope:
        value["scope"] = scope
    active_file_path = getattr(context, "active_file_path", None)
    if "activeFilePath" not in value and isinstance(active_file_path, str) and active_file_path:
        value["activeFilePath"] = active_file_path
    metadata = getattr(context, "metadata", {})
    if isinstance(metadata, Mapping) and "contextTasks" not in value and "tasks" in metadata:
        value["contextTasks"] = metadata["tasks"]
    return value


def _with_operation_context(input_data: Mapping[str, Any], context: Any) -> Mapping[str, Any]:
    value = dict(input_data)
    metadata = getattr(context, "metadata", {})
    if not isinstance(metadata, Mapping) or "tasks" not in metadata:
        return value
    operation_context = value.get("context")
    if not isinstance(operation_context, Mapping):
        operation_context = {}
    if "contextTasks" not in operation_context:
        value["context"] = {**operation_context, "contextTasks": metadata["tasks"]}
    return value
