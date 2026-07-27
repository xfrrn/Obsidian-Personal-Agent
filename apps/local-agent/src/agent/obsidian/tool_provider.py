"""Application 用例注册为 Agent Core 工具。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
from typing import Any, Awaitable, Callable, ClassVar, Mapping

from agent.permissions import ToolAccess
from agent.protocol.mode import ModeKind
from agent.tools.types import ToolExecutionContext, ToolSpec

from .read_note import ReadNoteUseCase
from .analyze_notes import (
    CheckVaultHealthUseCase,
    EvaluateRulesUseCase,
    ExtractTaskCandidatesUseCase,
    FindDuplicatesUseCase,
    FindRelatedNotesUseCase,
    InspectNoteUseCase,
    ListRulesUseCase,
    ListTagsUseCase,
)
from .build_operation_plan import BuildOperationPlanUseCase
from .search_notes import SearchNotesUseCase
from .list_tasks import ListTasksUseCase
from .analyze_project import AnalyzeProjectUseCase


class ToolPermission(str, Enum):
    READ = "read"
    WRITE = "write"


class ToolRiskLevel(str, Enum):
    LOW = "low"
    HIGH = "high"


class ToolEffect(str, Enum):
    READ = "read"
    PREPARE_WRITE = "prepare_write"


class ToolInvocationPolicy(str, Enum):
    PLANNER_ALLOWED = "planner_allowed"
    SYSTEM_ONLY = "system_only"


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, Any] = field(default_factory=lambda: {"type": "object"})
    permission: ToolPermission = ToolPermission.READ
    risk_level: ToolRiskLevel = ToolRiskLevel.LOW
    effect: ToolEffect = ToolEffect.READ
    invocation_policy: ToolInvocationPolicy = ToolInvocationPolicy.PLANNER_ALLOWED
    requires_confirmation: bool = False


@dataclass
class VaultAccessLedger:
    """记录模型在当前回合实际看到的路径，供写计划做可信边界校验。"""

    paths: dict[tuple[str | None, int | None], set[str]] = field(default_factory=dict)

    def allowed(self, context: ToolExecutionContext | None) -> set[str]:
        if context is None:
            return set()
        key = (context.session_id, context.submission_id)
        result = self.paths.setdefault(key, set())
        active = context.metadata.get("activeFilePath")
        if isinstance(active, str) and active:
            result.add(active)
        referenced = context.metadata.get("referencedPaths")
        if isinstance(referenced, (list, tuple)):
            result.update(path for path in referenced if isinstance(path, str) and path)
        return result

    def record(self, context: ToolExecutionContext | None, value: object) -> None:
        self.allowed(context).update(_paths_in(value))

    def clear(self, session_id: str | None, submission_id: int | None) -> None:
        self.paths.pop((session_id, submission_id), None)


@dataclass(frozen=True)
class FunctionTool:
    accepts_execution_context: ClassVar[bool] = True
    definition: ToolDefinition
    handler: Callable[[Mapping[str, Any], ToolExecutionContext | None], Awaitable[Any]]
    ledger: VaultAccessLedger
    supports_parallel_tool_calls: bool = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(self.definition.name, self.definition.description, dict(self.definition.input_schema))

    @property
    def required_access(self) -> ToolAccess:
        return ToolAccess.WORKSPACE_WRITE if self.definition.permission is ToolPermission.WRITE else ToolAccess.READ_ONLY

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
        context: ToolExecutionContext | None = None,
    ) -> str:
        if self.definition.name == "build_operation_plan" and mode is ModeKind.PLAN:
            raise PermissionError("Plan Mode cannot create executable Vault changes")
        value = _with_turn_context(arguments, context)
        _enforce_read_scope(value, context, self.ledger.allowed(context))
        if self.definition.name == "build_operation_plan":
            value = _with_operation_context(value, context, self.ledger.allowed(context))
        output = await self.handler(value, context)
        if self.definition.name in {"search_notes", "read_note"}:
            self.ledger.record(context, output)
        return json.dumps(output, ensure_ascii=False, default=str)


@dataclass(frozen=True)
class ObsidianToolDependencies:
    notes: object
    tasks: object
    operation_planner: object
    operation_plan_store: object
    operation_executor: object
    access_ledger: VaultAccessLedger = field(default_factory=VaultAccessLedger)


def build_obsidian_tools(deps: ObsidianToolDependencies) -> tuple[FunctionTool, ...]:
    """返回第一批由 Application 用例驱动的 Agent 工具。"""
    search_notes = SearchNotesUseCase(deps.notes)
    read_note = ReadNoteUseCase(deps.notes)
    list_tasks = ListTasksUseCase(deps.tasks)
    build_plan = BuildOperationPlanUseCase(deps.operation_planner, deps.operation_plan_store, deps.tasks)
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
            deps.access_ledger,
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
            deps.access_ledger,
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
            deps.access_ledger,
        ),
        FunctionTool(
            ToolDefinition(
                name="inspect_note",
                description="检查笔记规范、类型、链接、归档位置和缺失内容",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}, "activeFilePath": {"type": "string"}, "noteName": {"type": "string"}}},
            ),
            lambda input_data, _context: inspect_note.execute(input_data),
            deps.access_ledger,
        ),
        FunctionTool(
            ToolDefinition(
                name="analyze_project",
                description="汇总项目笔记、任务、状态、缺失文档和长期未更新内容",
                input_schema={"type": "object", "properties": {"project": {"type": "string"}, "projectName": {"type": "string"}, "activeFilePath": {"type": "string"}}},
            ),
            lambda input_data, _context: analyze_project.execute(input_data),
            deps.access_ledger,
        ),
        FunctionTool(
            ToolDefinition(
                name="check_vault_health",
                description="生成知识库规范和链接健康报告",
                input_schema={"type": "object", "properties": {"limit": {"type": "integer"}}},
            ),
            lambda input_data, _context: check_health.execute(input_data),
            deps.access_ledger,
        ),
        FunctionTool(
            ToolDefinition(name="list_tags", description="统计知识库标签、分类、别名和废弃状态"),
            lambda input_data, _context: list_tags.execute(input_data),
            deps.access_ledger,
        ),
        FunctionTool(
            ToolDefinition(
                name="find_related_notes",
                description="根据链接、标签、项目和词项重合查找相关笔记",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}, "activeFilePath": {"type": "string"}, "noteName": {"type": "string"}, "limit": {"type": "integer"}}},
            ),
            lambda input_data, _context: find_related.execute(input_data),
            deps.access_ledger,
        ),
        FunctionTool(
            ToolDefinition(
                name="find_duplicates",
                description="查找正文完全重复或高度相似的笔记",
                input_schema={"type": "object", "properties": {"threshold": {"type": "number"}, "scan_limit": {"type": "integer"}, "limit": {"type": "integer"}}},
            ),
            lambda input_data, _context: find_duplicates.execute(input_data),
            deps.access_ledger,
        ),
        FunctionTool(
            ToolDefinition(name="list_rules", description="列出当前知识库确定性管理规则"),
            lambda input_data, _context: list_rules.execute(input_data),
            deps.access_ledger,
        ),
        FunctionTool(
            ToolDefinition(
                name="evaluate_rules",
                description="对指定笔记或目录试运行确定性规则但不写入",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}, "activeFilePath": {"type": "string"}, "path_prefix": {"type": "string"}}},
            ),
            lambda input_data, _context: evaluate_rules.execute(input_data),
            deps.access_ledger,
        ),
        FunctionTool(
            ToolDefinition(
                name="extract_task_candidates",
                description="从笔记正文中提取尚未写成 Markdown Task 的潜在任务",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}, "activeFilePath": {"type": "string"}, "noteName": {"type": "string"}, "limit": {"type": "integer"}}},
            ),
            lambda input_data, _context: extract_tasks.execute(input_data),
            deps.access_ledger,
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
            lambda input_data, _context: build_plan.execute(input_data),
            deps.access_ledger,
        ),
    )


def _with_turn_context(
    input_data: Mapping[str, Any], context: ToolExecutionContext | None
) -> dict[str, Any]:
    value = dict(input_data)
    metadata = context.metadata if context is not None else {}
    raw_text = metadata.get("userInput")
    if isinstance(raw_text, str) and raw_text.strip():
        value["rawText"] = raw_text
    scope = metadata.get("scope")
    if isinstance(scope, str) and scope:
        value["scope"] = scope
    active_file_path = metadata.get("activeFilePath")
    if isinstance(active_file_path, str) and active_file_path:
        value["activeFilePath"] = active_file_path
    if "contextTasks" not in value and "tasks" in metadata:
        value["contextTasks"] = metadata["tasks"]
    return value


def _enforce_read_scope(
    input_data: Mapping[str, Any],
    context: ToolExecutionContext | None,
    allowed_paths: set[str],
) -> None:
    if context is None or context.metadata.get("scope") != "current":
        return
    requested = _input_paths(input_data)
    unauthorized = requested - allowed_paths
    if unauthorized:
        raise PermissionError(
            "current-note scope cannot read: " + ", ".join(sorted(unauthorized))
        )


def _input_paths(value: Mapping[str, Any]) -> set[str]:
    paths = {
        item
        for key in ("path", "notePath", "activeFilePath")
        if isinstance((item := value.get(key)), str) and item
    }
    batch = value.get("paths")
    if isinstance(batch, (list, tuple)):
        paths.update(item for item in batch if isinstance(item, str) and item)
    return paths


def _with_operation_context(
    input_data: Mapping[str, Any],
    context: ToolExecutionContext | None,
    allowed_paths: set[str],
) -> dict[str, Any]:
    value = dict(input_data)
    operation_context = value.get("context")
    if not isinstance(operation_context, Mapping):
        operation_context = {}
    metadata = context.metadata if context is not None else {}
    value["context"] = {
        **operation_context,
        "source": "interactive",
        "allowedPaths": sorted(allowed_paths) or ["__no_authorized_paths__"],
        "conversationId": context.session_id if context is not None else None,
        "submissionId": context.submission_id if context is not None else None,
        **({"contextTasks": metadata["tasks"]} if "tasks" in metadata else {}),
    }
    return value


def _paths_in(value: object) -> set[str]:
    if isinstance(value, Mapping):
        result = {
            path for key, path in value.items()
            if key == "path" and isinstance(path, str) and path
        }
        for item in value.values():
            result.update(_paths_in(item))
        return result
    if isinstance(value, (list, tuple)):
        result: set[str] = set()
        for item in value:
            result.update(_paths_in(item))
        return result
    return set()
