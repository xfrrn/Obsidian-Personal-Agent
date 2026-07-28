"""直接读写 Tasks 插件兼容的 Markdown 任务。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
import json
import os
from pathlib import Path
import re

from agent.config.settings import Settings
from agent.permissions import ToolAccess
from agent.protocol.mode import ModeKind
from agent.tools.handlers.note_files import note_path, read_note, write_note_if_unchanged
from agent.tools.types import ToolExecution, ToolSpec


_TASK = re.compile(
    r"^(?P<indent>[ \t]*)(?P<bullet>[-*+])[ \t]+\[(?P<status>[^\]\r\n])\][ \t]+(?P<body>.*)$"
)
_DUE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")
_RECURRENCE = re.compile(r"🔁\s*(.*?)(?=\s(?:📅|⏳|🛫|➕|✅|❌|🔺|⏫|🔼|🔽|⏬)|$)")
_TAG = re.compile(r"(?<![\w/])#([\w/-]+)")
_PRIORITY_SYMBOLS = {
    "highest": "🔺",
    "high": "⏫",
    "medium": "🔼",
    "normal": "",
    "low": "🔽",
    "lowest": "⏬",
}
_PRIORITY = re.compile(r"\s*(🔺|⏫|🔼|🔽|⏬️?)")


class QueryTasksTool:
    """扫描 Markdown 任务并返回稳定的文件与行号定位。"""

    supports_parallel_tool_calls = True
    required_access = ToolAccess.READ_ONLY
    spec = ToolSpec(
        name="query_tasks",
        description=(
            "查询 Vault 中兼容 Obsidian Tasks 的 Markdown 任务。"
            "可按完成状态、路径、截止日期和标签过滤。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["open", "done", "any"]},
                "path_prefix": {"type": "string"},
                "due_before": {"type": "string", "description": "包含该 YYYY-MM-DD 日期。"},
                "due_after": {"type": "string", "description": "包含该 YYYY-MM-DD 日期。"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "additionalProperties": False,
        },
    )

    def __init__(self, settings: Settings) -> None:
        self._workspace = settings.workspace.resolve()

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> ToolExecution:
        filters = _query_arguments(arguments)
        tasks = [
            task
            for task in _vault_tasks(self._workspace, filters[1])
            if _matches(task, filters[0], filters[2], filters[3], filters[4])
        ]
        tasks.sort(key=lambda task: (task["due"] is None, task["due"] or "", task["path"], task["line"]))
        limit = filters[5]
        return ToolExecution(
            json.dumps(
                {
                    "tasks": tasks[:limit],
                    "count": len(tasks),
                    "truncated": len(tasks) > limit,
                },
                ensure_ascii=False,
            )
        )


class MutateTaskTool:
    """创建或精确修改一条 Markdown 任务。"""

    supports_parallel_tool_calls = False
    required_access = ToolAccess.WORKSPACE_WRITE
    spec = ToolSpec(
        name="mutate_task",
        description=(
            "创建或修改一条 Obsidian Tasks 兼容任务。"
            "修改既有任务必须提供 query_tasks 返回的行号和原文。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "complete", "reopen", "reschedule", "set_priority"],
                },
                "path": {"type": "string"},
                "line": {"type": "integer", "minimum": 1},
                "expected_text": {"type": "string"},
                "description": {"type": "string"},
                "heading": {"type": "string", "description": "create 默认使用 ## Tasks。"},
                "due": {"type": "string", "description": "YYYY-MM-DD。"},
                "priority": {
                    "type": "string",
                    "enum": ["highest", "high", "medium", "normal", "low", "lowest"],
                },
                "recurrence": {"type": "string"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                },
            },
            "required": ["action", "path"],
            "additionalProperties": False,
        },
    )

    def __init__(self, settings: Settings) -> None:
        self._workspace = settings.workspace.resolve()

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> ToolExecution:
        if granted_access is not ToolAccess.WORKSPACE_WRITE:
            raise PermissionError("本次任务修改尚未获得工作区写入权限")
        action, target, options = _mutation_arguments(self._workspace, arguments)
        original, text = read_note(target)
        if action == "create":
            updated, line_number, task_text = _create_task(text, options)
        else:
            updated, line_number, task_text = _mutate_task(text, action, options)
        write_note_if_unchanged(target, original, updated.encode("utf-8"))
        return ToolExecution(
            json.dumps(
                {
                    "action": action,
                    "path": target.relative_to(self._workspace).as_posix(),
                    "line": line_number,
                    "text": task_text,
                },
                ensure_ascii=False,
            )
        )


def _query_arguments(
    arguments: dict[str, object],
) -> tuple[str, str, date | None, date | None, set[str], int]:
    unknown = set(arguments) - {"status", "path_prefix", "due_before", "due_after", "tags", "limit"}
    if unknown:
        raise ValueError(f"包含未知字段: {', '.join(sorted(unknown))}")
    status = arguments.get("status", "open")
    if status not in {"open", "done", "any"}:
        raise ValueError("status 必须是 open、done 或 any")
    path_prefix = _path_prefix(arguments.get("path_prefix", ""))
    due_before = _optional_date(arguments.get("due_before"), "due_before")
    due_after = _optional_date(arguments.get("due_after"), "due_after")
    if due_before and due_after and due_after > due_before:
        raise ValueError("due_after 不能晚于 due_before")
    tags = {tag.casefold() for tag in _tags(arguments.get("tags", []))}
    limit = arguments.get("limit", 100)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
        raise ValueError("limit 必须是 1 到 500 的整数")
    return status, path_prefix, due_before, due_after, tags, limit


def _vault_tasks(workspace: Path, path_prefix: str) -> Iterator[dict[str, object]]:
    for root, directories, files in os.walk(workspace):
        directories[:] = sorted(name for name in directories if not name.startswith("."))
        for name in sorted(files):
            if not name.lower().endswith(".md") or name.startswith("."):
                continue
            path = Path(root) / name
            try:
                relative = path.resolve().relative_to(workspace).as_posix()
                if path.is_symlink() or not relative.lower().startswith(path_prefix.lower()):
                    continue
                if path.stat().st_size > 2_000_000:
                    continue
                text = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            for line_number, line in enumerate(text.splitlines(), 1):
                match = _TASK.match(line)
                if match is not None:
                    yield _task_result(relative, line_number, line, match)


def _task_result(
    path: str, line_number: int, line: str, match: re.Match[str]
) -> dict[str, object]:
    body = match.group("body")
    due_match = _DUE.search(body)
    recurrence_match = _RECURRENCE.search(body)
    symbol = match.group("status")
    # ponytail: 未读取 Tasks 自定义状态配置；需要时再通过插件配置映射状态类型。
    done = symbol.lower() == "x"
    return {
        "path": path,
        "line": line_number,
        "text": line,
        "status_symbol": symbol,
        "done": done,
        "due": due_match.group(1) if due_match else None,
        "priority": _priority_name(body),
        "tags": [f"#{tag}" for tag in _TAG.findall(body)],
        "recurrence": recurrence_match.group(1).strip() if recurrence_match else None,
    }


def _matches(
    task: dict[str, object],
    status: str,
    due_before: date | None,
    due_after: date | None,
    tags: set[str],
) -> bool:
    done = bool(task["done"])
    if status == "open" and done or status == "done" and not done:
        return False
    raw_due = task["due"]
    if due_before or due_after:
        if not isinstance(raw_due, str):
            return False
        try:
            due = date.fromisoformat(raw_due)
        except ValueError:
            return False
        if due_before and due > due_before or due_after and due < due_after:
            return False
    task_tags = {str(tag).casefold() for tag in task["tags"]}
    return not tags or tags <= task_tags


def _mutation_arguments(
    workspace: Path, arguments: dict[str, object]
) -> tuple[str, Path, dict[str, object]]:
    action = arguments.get("action")
    allowed = {
        "create": {"action", "path", "description", "heading", "due", "priority", "recurrence", "tags"},
        "complete": {"action", "path", "line", "expected_text"},
        "reopen": {"action", "path", "line", "expected_text"},
        "reschedule": {"action", "path", "line", "expected_text", "due"},
        "set_priority": {"action", "path", "line", "expected_text", "priority"},
    }
    if action not in allowed:
        raise ValueError("action 必须是 create、complete、reopen、reschedule 或 set_priority")
    if unknown := set(arguments) - allowed[action]:
        raise ValueError(f"{action} 包含无效字段: {', '.join(sorted(unknown))}")
    target = note_path(workspace, arguments.get("path"))
    if action == "create":
        options = {
            "description": _single_line(arguments.get("description"), "description", 2_000),
            "heading": _heading(arguments.get("heading", "## Tasks")),
            "due": _optional_date_text(arguments.get("due"), "due"),
            "priority": _priority(arguments.get("priority", "normal")),
            "recurrence": _optional_line(arguments.get("recurrence"), "recurrence", 200),
            "tags": sorted(_tags(arguments.get("tags", []))),
        }
        return action, target, options

    line = arguments.get("line")
    if not isinstance(line, int) or isinstance(line, bool) or line < 1:
        raise ValueError("line 必须是正整数")
    options = {
        "line": line,
        "expected_text": _exact_line(arguments.get("expected_text")),
    }
    if action == "reschedule":
        options["due"] = _optional_date_text(arguments.get("due"), "due", required=True)
    if action == "set_priority":
        options["priority"] = _priority(arguments.get("priority"))
    return action, target, options


def _create_task(text: str, options: dict[str, object]) -> tuple[str, int, str]:
    newline = "\r\n" if "\r\n" in text else "\n"
    task = f"- [ ] {options['description']}"
    tags = options["tags"]
    assert isinstance(tags, list)
    if tags:
        task += " " + " ".join(tags)
    priority = _PRIORITY_SYMBOLS[str(options["priority"])]
    if priority:
        task += f" {priority}"
    if options["recurrence"]:
        task += f" 🔁 {options['recurrence']}"
    if options["due"]:
        task += f" 📅 {options['due']}"

    heading = str(options["heading"])
    lines = text.splitlines(keepends=True)
    heading_index = next(
        (index for index, line in enumerate(lines) if line.rstrip("\r\n") == heading),
        None,
    )
    if heading_index is None:
        separator = "" if not text else newline if text.endswith(("\n", "\r")) else newline * 2
        updated = f"{text}{separator}{heading}{newline}{newline}{task}{newline}"
        return updated, len(updated.splitlines()), task

    level = len(heading) - len(heading.lstrip("#"))
    insert_at = len(lines)
    for index in range(heading_index + 1, len(lines)):
        match = re.match(r"^(#{1,6})[ \t]+", lines[index])
        if match and len(match.group(1)) <= level:
            insert_at = index
            break
    while insert_at > heading_index + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    if insert_at and not lines[insert_at - 1].endswith(("\n", "\r")):
        lines[insert_at - 1] += newline
    lines.insert(insert_at, task + newline)
    return "".join(lines), insert_at + 1, task


def _mutate_task(
    text: str, action: str, options: dict[str, object]
) -> tuple[str, int, str]:
    lines = text.splitlines(keepends=True)
    line_number = int(options["line"])
    if line_number > len(lines):
        raise ValueError("任务行号已超出文件范围，请重新调用 query_tasks")
    original_line = lines[line_number - 1]
    ending = original_line[len(original_line.rstrip("\r\n")) :]
    raw = original_line.removesuffix(ending) if ending else original_line
    if raw != options["expected_text"]:
        raise ValueError("任务原文已经变化，请重新调用 query_tasks")
    match = _TASK.match(raw)
    if match is None:
        raise ValueError("目标行不是 Markdown 任务")

    if action in {"complete", "reopen"}:
        if action == "complete" and _RECURRENCE.search(match.group("body")):
            raise ValueError("循环任务必须通过 Tasks 插件完成，以便生成下一次任务")
        symbol = "x" if action == "complete" else " "
        changed = raw[: match.start("status")] + symbol + raw[match.end("status") :]
    else:
        body = match.group("body")
        if action == "reschedule":
            due_matches = _DUE.findall(body)
            if len(due_matches) > 1:
                raise ValueError("任务包含多个截止日期，拒绝自动修改")
            due = str(options["due"])
            body = _DUE.sub(f"📅 {due}", body) if due_matches else f"{body} 📅 {due}"
        else:
            body = _PRIORITY.sub("", body).rstrip()
            priority = _PRIORITY_SYMBOLS[str(options["priority"])]
            if priority:
                body += f" {priority}"
        changed = raw[: match.start("body")] + body
    if changed == raw:
        raise ValueError("任务没有发生变化")
    lines[line_number - 1] = changed + ending
    return "".join(lines), line_number, changed


def _path_prefix(value: object) -> str:
    if value == "":
        return ""
    text = _single_line(value, "path_prefix", 1_000).replace("\\", "/").strip("/")
    if any(part.startswith(".") for part in text.split("/")):
        raise ValueError("path_prefix 不能包含隐藏目录或路径跳转")
    return text


def _tags(value: object) -> set[str]:
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError("tags 必须是最多包含 20 项的数组")
    result: dict[str, str] = {}
    for item in value:
        tag = _single_line(item, "tag", 100).lstrip("#")
        if re.fullmatch(r"[\w/-]+", tag) is None or tag.isdigit():
            raise ValueError(f"无效标签: {tag}")
        rendered = f"#{tag}"
        result.setdefault(rendered.casefold(), rendered)
    return set(result.values())


def _optional_date(value: object, name: str) -> date | None:
    text = _optional_date_text(value, name)
    return date.fromisoformat(text) if text else None


def _optional_date_text(value: object, name: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{name} 是必填字段")
        return None
    text = _single_line(value, name, 10)
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"{name} 必须是有效的 YYYY-MM-DD 日期") from exc


def _priority(value: object) -> str:
    if value not in _PRIORITY_SYMBOLS:
        raise ValueError("priority 必须是 highest、high、medium、normal、low 或 lowest")
    return str(value)


def _priority_name(body: str) -> str:
    for name, symbol in _PRIORITY_SYMBOLS.items():
        if symbol and symbol in body:
            return name
    return "normal"


def _heading(value: object) -> str:
    text = _single_line(value, "heading", 200)
    if re.fullmatch(r"#{1,6}[ \t]+[^#].*", text) is None:
        raise ValueError("heading 必须是 Markdown 标题，例如 ## Tasks")
    return text


def _optional_line(value: object, name: str, limit: int) -> str | None:
    return None if value is None else _single_line(value, name, limit)


def _single_line(value: object, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    text = value.strip()
    if len(text) > limit or "\n" in text or "\r" in text:
        raise ValueError(f"{name} 必须是不超过 {limit} 字符的单行文本")
    return text


def _exact_line(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 10_000:
        raise ValueError("expected_text 必须是非空任务原文")
    if "\n" in value or "\r" in value:
        raise ValueError("expected_text 不能包含换行")
    return value
