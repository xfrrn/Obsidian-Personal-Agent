"""为当前 Vault 中的既有笔记创建固定 frontmatter。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
import json
import os
from pathlib import Path
from uuid import uuid4

from agent.config.settings import Settings
from agent.permissions import ToolAccess
from agent.protocol.mode import ModeKind
from agent.tools.types import ToolExecution, ToolSpec


class CreateFrontmatterTool:
    """在既有 Markdown 正文前添加 Properties，不改写已有 frontmatter。"""

    supports_parallel_tool_calls = False
    required_access = ToolAccess.WORKSPACE_WRITE
    spec = ToolSpec(
        name="create_frontmatter",
        description=(
            "为当前 Obsidian Vault 中的既有 Markdown 笔记创建 YAML frontmatter。"
            "自动写入 created，并按 title、status、created、tags 的固定顺序排列；"
            "笔记已有 frontmatter 时拒绝执行。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Vault 内既有 .md 笔记的相对路径。",
                },
                "title": {"type": "string", "description": "笔记标题。"},
                "status": {
                    "type": "string",
                    "enum": ["todo", "doing", "done"],
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 50,
                    "description": "可选标签列表。",
                },
            },
            "required": ["path", "title", "status"],
            "additionalProperties": False,
        },
    )

    def __init__(
        self, settings: Settings, today: Callable[[], date] | None = None
    ) -> None:
        self._workspace = settings.workspace.resolve()
        self._today = today or date.today

    async def run(
        self,
        arguments: dict[str, object],
        *,
        granted_access: ToolAccess | None = None,
        mode: ModeKind = ModeKind.DEFAULT,
    ) -> ToolExecution:
        if granted_access is not ToolAccess.WORKSPACE_WRITE:
            raise PermissionError("本次 frontmatter 创建尚未获得工作区写入权限")
        target, title, status, tags = _parse_arguments(self._workspace, arguments)
        if not target.is_file():
            raise ValueError(f"笔记不存在: {target.relative_to(self._workspace)}")

        original = target.read_bytes()
        body = original.removeprefix(b"\xef\xbb\xbf")
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("笔记必须使用 UTF-8 编码") from exc
        if text.startswith("---\n") or text.startswith("---\r\n"):
            raise ValueError("笔记已经包含 frontmatter，不会重复创建")

        created = self._today()
        content = _render_frontmatter(title, status, created, tags).encode("utf-8") + body
        temporary = target.with_name(f".{target.name}.frontmatter-{uuid4().hex}")
        try:
            temporary.write_bytes(content)
            if target.read_bytes() != original:
                raise RuntimeError("笔记在写入期间发生变化，未创建 frontmatter")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

        relative_path = target.relative_to(self._workspace).as_posix()
        return ToolExecution(
            json.dumps(
                {"path": relative_path, "created": created.isoformat()},
                ensure_ascii=False,
            )
        )


def _parse_arguments(
    workspace: Path, arguments: dict[str, object]
) -> tuple[Path, str, str, list[str]]:
    unknown = set(arguments) - {"path", "title", "status", "tags"}
    if unknown:
        raise ValueError(f"包含未知字段: {', '.join(sorted(unknown))}")

    target = _note_path(workspace, arguments.get("path"))
    title = _text(arguments.get("title"), "title", 500)
    status = arguments.get("status")
    if status not in {"todo", "doing", "done"}:
        raise ValueError("status 必须是 todo、doing 或 done")

    raw_tags = arguments.get("tags", [])
    if not isinstance(raw_tags, list):
        raise ValueError("tags 必须是字符串数组")
    if len(raw_tags) > 50:
        raise ValueError("tags 不能超过 50 个")
    tags = [_text(value, f"tags[{index}]", 100) for index, value in enumerate(raw_tags)]
    return target, title, status, tags


def _note_path(workspace: Path, value: object) -> Path:
    path_text = _text(value, "path", 1_000)
    raw_path = Path(path_text)
    if raw_path.is_absolute() or raw_path.drive:
        raise ValueError("path 必须是 Vault 内的相对路径")
    if any(part.startswith(".") for part in raw_path.parts):
        raise ValueError("path 不能包含隐藏目录或路径跳转")
    if raw_path.suffix.lower() != ".md":
        raise ValueError("path 必须以 .md 结尾")
    try:
        target = (workspace / raw_path).resolve()
    except OSError as exc:
        raise ValueError("path 不是有效路径") from exc
    if not target.is_relative_to(workspace):
        raise ValueError("path 超出 Vault")
    return target


def _text(value: object, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    resolved = value.strip()
    if len(resolved) > limit:
        raise ValueError(f"{name} 不能超过 {limit} 个字符")
    if "\n" in resolved or "\r" in resolved:
        raise ValueError(f"{name} 不能包含换行")
    return resolved


def _render_frontmatter(
    title: str, status: str, created: date, tags: list[str]
) -> str:
    lines = [
        "---",
        f"title: {_yaml_scalar(title)}",
        f"status: {status}",
        f"created: {created.isoformat()}",
    ]
    if tags:
        lines.append("tags:")
        lines.extend(f"  - {_yaml_scalar(tag)}" for tag in tags)
    else:
        lines.append("tags: []")
    return "\n".join((*lines, "---", "", ""))


def _yaml_scalar(value: str) -> str:
    ambiguous = {"null", "true", "false", "yes", "no", "on", "off", "~"}
    plain = value[0].isalpha() and all(
        character.isalnum() or character in " _-/+." for character in value
    )
    return value if plain and value.lower() not in ambiguous else json.dumps(value, ensure_ascii=False)
