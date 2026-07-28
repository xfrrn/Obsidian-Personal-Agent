"""安全更新既有笔记的扁平 Obsidian Properties。"""

from __future__ import annotations

from datetime import date
import json
import math
from pathlib import Path
import re

from agent.config.settings import Settings
from agent.permissions import ToolAccess
from agent.protocol.mode import ModeKind
from agent.tools.handlers.note_files import note_path, read_note, write_note_if_unchanged, yaml_scalar
from agent.tools.types import ToolExecution, ToolSpec


_PROPERTY_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}")


class UpdatePropertiesTool:
    """更新指定顶层字段，同时保留正文和未指定字段。"""

    supports_parallel_tool_calls = False
    required_access = ToolAccess.WORKSPACE_WRITE
    spec = ToolSpec(
        name="update_properties",
        description=(
            "更新既有 Markdown 笔记 frontmatter 中的扁平 Properties。"
            "支持字符串、数字、布尔值和字符串数组；保留正文及未指定字段。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Vault 内既有 .md 笔记路径。"},
                "set": {
                    "type": "object",
                    "additionalProperties": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "number"},
                            {"type": "boolean"},
                            {"type": "array", "items": {"type": "string"}, "maxItems": 100},
                        ]
                    },
                    "description": "要新增或替换的顶层 Properties。",
                },
                "remove": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 100,
                    "description": "要删除的顶层 Property 名称。",
                },
            },
            "required": ["path"],
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
            raise PermissionError("本次 Properties 更新尚未获得工作区写入权限")
        target, values, removed = _parse_arguments(self._workspace, arguments)
        original, text = read_note(target)
        updated = _update_frontmatter(text, values, removed)
        if updated == text:
            raise ValueError("Properties 没有发生变化")
        write_note_if_unchanged(target, original, updated.encode("utf-8"))
        return ToolExecution(
            json.dumps(
                {
                    "path": target.relative_to(self._workspace).as_posix(),
                    "set": list(values),
                    "removed": sorted(removed),
                },
                ensure_ascii=False,
            )
        )


def _parse_arguments(
    workspace: Path, arguments: dict[str, object]
) -> tuple[Path, dict[str, object], set[str]]:
    unknown = set(arguments) - {"path", "set", "remove"}
    if unknown:
        raise ValueError(f"包含未知字段: {', '.join(sorted(unknown))}")
    target = note_path(workspace, arguments.get("path"))

    raw_values = arguments.get("set", {})
    if not isinstance(raw_values, dict):
        raise ValueError("set 必须是对象")
    if len(raw_values) > 100:
        raise ValueError("set 不能超过 100 个字段")
    values: dict[str, object] = {}
    for key, value in raw_values.items():
        name = _property_name(key)
        values[name] = _property_value(value, name)

    raw_removed = arguments.get("remove", [])
    if not isinstance(raw_removed, list) or len(raw_removed) > 100:
        raise ValueError("remove 必须是最多包含 100 项的数组")
    removed = {_property_name(value) for value in raw_removed}
    if overlap := set(values) & removed:
        raise ValueError(f"字段不能同时设置和删除: {', '.join(sorted(overlap))}")
    if not values and not removed:
        raise ValueError("set 和 remove 至少提供一项")
    return target, values, removed


def _property_name(value: object) -> str:
    if not isinstance(value, str) or _PROPERTY_NAME.fullmatch(value) is None:
        raise ValueError("Property 名称只能包含字母、数字、下划线和连字符")
    return value


def _property_value(value: object, name: str) -> object:
    if isinstance(value, str):
        if not value.strip() or len(value) > 2_000 or "\n" in value or "\r" in value:
            raise ValueError(f"{name} 必须是 1 到 2000 字符的单行字符串")
        return value.strip()
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{name} 必须是有限数字")
        return value
    if isinstance(value, list) and len(value) <= 100:
        return [_property_value(item, f"{name}[]") for item in value if isinstance(item, str)] if all(isinstance(item, str) for item in value) else _invalid_value(name)
    return _invalid_value(name)


def _invalid_value(name: str) -> object:
    raise ValueError(f"{name} 只支持字符串、数字、布尔值或字符串数组")


def _update_frontmatter(text: str, values: dict[str, object], removed: set[str]) -> str:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ValueError("笔记没有 frontmatter，请先调用 create_frontmatter")
    closing = next(
        (index for index, line in enumerate(lines[1:], 1) if line.rstrip("\r\n") == "---"),
        None,
    )
    if closing is None:
        raise ValueError("frontmatter 缺少结束分隔符")
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    targets = set(values) | removed
    counts = {name: 0 for name in targets}
    for line in lines[1:closing]:
        if (key := _line_key(line)) in counts:
            counts[key] += 1
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    if duplicates:
        raise ValueError(f"frontmatter 包含重复字段: {', '.join(duplicates)}")

    result = [lines[0]]
    seen: set[str] = set()
    index = 1
    while index < closing:
        key = _line_key(lines[index])
        if key not in targets:
            result.append(lines[index])
            index += 1
            continue
        end = _property_end(lines, index + 1, closing)
        if key in values:
            result.extend(_render_property(key, values[key], newline))
        seen.add(key)
        index = end
    for key, value in values.items():
        if key not in seen:
            result.extend(_render_property(key, value, newline))
    result.extend(lines[closing:])
    return "".join(result)


def _line_key(line: str) -> str | None:
    raw = line.rstrip("\r\n")
    if not raw or raw[0].isspace() or raw.startswith("#") or ":" not in raw:
        return None
    candidate = raw.partition(":")[0].strip()
    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in "\"'":
        candidate = candidate[1:-1]
    return candidate if _PROPERTY_NAME.fullmatch(candidate) else None


def _property_end(lines: list[str], start: int, closing: int) -> int:
    index = start
    while index < closing:
        line = lines[index]
        if _line_key(line) is not None:
            break
        if line.strip() and not line[0].isspace():
            break
        index += 1
    return index


def _render_property(key: str, value: object, newline: str) -> list[str]:
    if isinstance(value, list):
        if not value:
            return [f"{key}: []{newline}"]
        return [f"{key}:{newline}", *(f"  - {yaml_scalar(item)}{newline}" for item in value)]
    if isinstance(value, bool):
        rendered = str(value).lower()
    elif isinstance(value, (int, float)):
        rendered = json.dumps(value)
    elif key == "created" and _is_iso_date(value):
        rendered = value
    else:
        assert isinstance(value, str)
        rendered = yaml_scalar(value)
    return [f"{key}: {rendered}{newline}"]


def _is_iso_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False
