"""受工作目录约束的文本补丁工具，兼容 Codex 和 unified diff 格式。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import unicodedata
from uuid import uuid4

from agent.config.settings import Settings
from agent.permissions import ToolAccess
from agent.protocol.mode import ModeKind
from agent.tools.types import ToolExecutionContext, ToolSpec


_HUNK_HEADER = re.compile(
    r"@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?:.*)"
)
_END_PATCH = re.compile(r"(?:\*{3}\s*)?End(?:\s+of)?\s+Patch(?:\s*\*{3})?", re.IGNORECASE)
_HEREDOC = re.compile(r"<<-?\s*(?P<quote>['\"]?)(?P<marker>[A-Za-z_][A-Za-z0-9_]*)\1")
_TYPOGRAPHIC_DASHES = "–—−"
_TYPOGRAPHIC_CHARACTERS = str.maketrans(
    {
        "\u00a0": " ",
        "\u2007": " ",
        "\u202f": " ",
        "‘": "'",
        "’": "'",
        "‚": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "–": "-",
        "—": "-",
        "−": "-",
    }
)


@dataclass(frozen=True, slots=True)
class _Hunk:
    anchor: str | None
    end_of_file: bool
    position_hint: int | None
    lines: tuple[tuple[str, str], ...]
    raw_replacement: bool = False


@dataclass(frozen=True, slots=True)
class _FilePatch:
    old_path: str | None
    new_path: str | None
    hunks: tuple[_Hunk, ...]
    delete_only: bool = False


@dataclass(frozen=True, slots=True)
class _PlannedChange:
    path: Path
    content: str | None
    action: str
    summary: str | None = None


class ApplyPatchTool:
    """在预校验全部 hunk 后，原子地修改工作目录中的 UTF-8 文本文件。"""

    # 补丁会读写同一工作区；与任何其他工具并发都可能使校验结果过期。
    supports_parallel_tool_calls = False
    required_access = ToolAccess.WORKSPACE_WRITE

    spec = ToolSpec(
        name="apply_patch",
        description=(
            "修改工作目录内的 UTF-8 文本文件。优先使用 Codex 补丁格式：\n"
            "*** Begin Patch\n*** Update File: path/to/file.py\n@@ def function():\n"
            "-old line\n+new line\n*** End Patch\n"
            "支持 Add File、Delete File、Move to 和 *** End of File；会按精确、忽略尾部空白、"
            "忽略首尾空白、Unicode 标点规范化依次匹配上下文。每行必须以空格、+ 或 - 开头；"
            "+/- 后必须紧跟实际内容，不能添加分隔空格。结束标记必须独占一行。"
            "新增文件才可直接提供未加 + 的完整内容。也兼容现有 unified diff。"
            "末尾追加示例：@@\\n last existing line\\n+new line\\n*** End of File。"
            "新增 Markdown bullet 必须写成 `+- item`。"
            "空文件写入示例：@@\\n+first line\\n*** End of File。"
            "本工具只修改文件，读取文件请使用只读命令。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "patch": {
                    "type": "string",
                    "description": "要应用的 Codex 风格补丁；所有路径必须相对工作目录。",
                }
            },
            "required": ["patch"],
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
        context: ToolExecutionContext | None = None,
    ) -> str:
        patch = arguments.get("patch")
        if not isinstance(patch, str) or not patch.strip():
            raise ValueError("patch 必须是非空补丁字符串")

        planned_changes = _plan_changes(_parse_patch(patch), self._workspace)
        for change in planned_changes:
            _write_change(change)
        summary = ", ".join(
            change.summary
            or f"{change.action} {change.path.relative_to(self._workspace)}"
            for change in planned_changes
            if change.summary is not None or change.action
        )
        return f"已应用补丁：{summary}"


def _parse_patch(patch: str) -> tuple[_FilePatch, ...]:
    lines = _extract_patch_lines(patch)
    while lines and not lines[0]:
        lines.pop(0)
    if lines and lines[0] == "*** Begin Patch":
        return _parse_codex_patch(lines)
    return _parse_unified_diff(lines)


def _extract_patch_lines(patch: str) -> list[str]:
    """剥离模型偶尔附加的 heredoc 外壳，避免 shell 语法被误当成补丁内容。"""

    lines = patch.splitlines()
    for index, line in enumerate(lines):
        if line == "*** Begin Patch":
            for end in range(index + 1, len(lines)):
                if _END_PATCH.fullmatch(lines[end].strip()):
                    # Models occasionally reorder the marker words. Normalize only this
                    # unambiguous whole-line boundary instead of relaxing all directives.
                    return [*lines[index:end], "*** End Patch"]
            if lines[-1].strip() == "*** End of File":
                # A trailing EOF directive makes the intended patch boundary explicit.
                return [*lines[index:], "*** End Patch"]
            raise ValueError("Codex 补丁缺少 *** End Patch")

        if line.startswith("--- "):
            break
        match = _HEREDOC.search(line)
        if match is None:
            continue
        marker = match["marker"]
        for end in range(index + 1, len(lines)):
            if lines[end].strip() == marker:
                return lines[index + 1 : end]
        raise ValueError(f"heredoc 缺少结束标记: {marker}")
    return lines


def _parse_codex_patch(lines: list[str]) -> tuple[_FilePatch, ...]:
    file_patches: list[_FilePatch] = []
    index = 1
    while index < len(lines):
        line = lines[index]
        if line == "*** End Patch":
            if index != len(lines) - 1:
                raise ValueError("*** End Patch 后不能包含其他内容")
            break
        if line.startswith("*** Add File: "):
            file_patch, index = _parse_add_file(lines, index)
        elif line.startswith("*** Delete File: "):
            file_patch, index = _parse_delete_file(lines, index)
        elif line.startswith("*** Update File: "):
            file_patch, index = _parse_update_file(lines, index)
        elif line.startswith("*** Move to: ") and " -> " in line:
            move = line.removeprefix("*** Move to: ")
            old_path, new_path = move.split(" -> ", maxsplit=1)
            if not old_path.strip() or not new_path.strip():
                raise ValueError(f"补丁第 {index + 1} 行的移动路径无效")
            # Normalize an unambiguous model shorthand into the documented form.
            lines[index : index + 1] = [
                f"*** Update File: {old_path.strip()}",
                f"*** Move to: {new_path.strip()}",
            ]
            file_patch, index = _parse_update_file(lines, index)
        else:
            raise ValueError(f"补丁第 {index + 1} 行应为文件操作")
        file_patches.append(file_patch)
    else:
        raise ValueError("Codex 补丁缺少 *** End Patch")

    if not file_patches:
        raise ValueError("补丁中未找到文件修改")
    return tuple(file_patches)


def _parse_add_file(lines: list[str], index: int) -> tuple[_FilePatch, int]:
    path = _directive_path(lines[index], "*** Add File: ")
    index += 1
    content: list[str] = []
    prefixed: bool | None = None
    while index < len(lines) and not lines[index].startswith("*** "):
        line = lines[index]
        if line == r"\ No newline at end of file":
            index += 1
            continue
        line_is_prefixed = line.startswith("+")
        if prefixed is None:
            prefixed = line_is_prefixed
        elif prefixed != line_is_prefixed:
            raise ValueError(f"补丁第 {index + 1} 行不能混用带 + 和未带 + 的新增内容")
        content.append(line[1:] if line_is_prefixed else line)
        index += 1
    return _FilePatch(
        None,
        path,
        (_Hunk(None, False, 0, tuple(("+", line) for line in content)),),
    ), index


def _parse_delete_file(lines: list[str], index: int) -> tuple[_FilePatch, int]:
    path = _directive_path(lines[index], "*** Delete File: ")
    index += 1
    if index < len(lines) and not lines[index].startswith("*** "):
        raise ValueError("*** Delete File 不能包含 hunk 内容")
    return _FilePatch(path, None, (), delete_only=True), index


def _parse_update_file(lines: list[str], index: int) -> tuple[_FilePatch, int]:
    old_path = _directive_path(lines[index], "*** Update File: ")
    new_path = old_path
    hunks: list[_Hunk] = []
    index += 1
    while index < len(lines):
        line = lines[index]
        if line.startswith("@@"):
            hunk, index = _parse_context_hunk(lines, index)
            if hunk.lines or hunk.end_of_file:
                hunks.append(hunk)
            continue
        if line == "*** End of File":
            # Models sometimes place the EOF directive where the @@ header belongs.
            lines.insert(index, "@@")
            continue
        if line.startswith("*** Move to: "):
            if new_path != old_path:
                raise ValueError("每个 Update File 只能包含一个 *** Move to")
            new_path = _directive_path(line, "*** Move to: ")
            index += 1
            continue
        if line.startswith("*** "):
            break
        raise ValueError(f"补丁第 {index + 1} 行应为 @@ hunk 或文件操作")
    if not hunks and new_path == old_path:
        raise ValueError("*** Update File 至少需要一个 @@ hunk")
    return _FilePatch(old_path, new_path, tuple(hunks)), index


def _parse_context_hunk(lines: list[str], index: int) -> tuple[_Hunk, int]:
    header = lines[index]
    numeric_header = _HUNK_HEADER.fullmatch(header)
    header_label = header[2:].strip().removesuffix(":").strip()
    header_marks_eof = header_label == "*** End of File"
    anchor = None if numeric_header or header_marks_eof else header_label or None
    position_hint = (
        max(int(numeric_header["old_start"]) - 1, 0) if numeric_header is not None else None
    )
    index += 1
    hunk_lines: list[tuple[str, str]] = []
    raw_lines: list[str] | None = None
    end_of_file = header_marks_eof
    while index < len(lines):
        line = lines[index]
        if line == "*** End of File":
            end_of_file = True
            index += 1
            continue
        if line.startswith(("@@", "*** ")):
            break
        if line == r"\ No newline at end of file":
            index += 1
            continue
        if not line or line[0] not in {" ", "+", "-"}:
            if hunk_lines:
                raise ValueError(f"补丁第 {index + 1} 行不能混用 diff 和裸内容")
            if raw_lines is None:
                raw_lines = []
            raw_lines.append(line)
            index += 1
            continue
        if raw_lines is not None:
            # A bare line followed by prefixed lines is usually omitted context syntax,
            # not a full-file replacement. Treat it as context and keep the strict raw
            # replacement path only for hunks that remain entirely bare.
            hunk_lines.extend((" ", raw_line) for raw_line in raw_lines)
            raw_lines = None
        hunk_lines.append((line[0], line[1:]))
        index += 1
    if raw_lines is not None:
        if anchor is None:
            raise ValueError("未加前缀的 Update File 内容必须提供 @@ 锚点")
        # Some models emit a complete replacement after an Update header. Keep this narrow
        # compatibility path separate from diff matching so it cannot silently merge both forms.
        return _Hunk(anchor, False, position_hint, tuple(("+", line) for line in raw_lines), True), index
    if numeric_header is not None and hunk_lines and all(kind == " " for kind, _ in hunk_lines):
        old_count = int(numeric_header["old_count"] or 1)
        new_count = int(numeric_header["new_count"] or 1)
        if old_count and new_count and len(hunk_lines) == old_count + new_count:
            # Some models emit old/new blocks under a numeric header but omit their -/+
            # markers. The declared counts make this exact split recoverable.
            content = [line for _, line in hunk_lines]
            hunk_lines = [
                *(("-", line) for line in content[:old_count]),
                *(("+", line) for line in content[old_count:]),
            ]
    return _Hunk(anchor, end_of_file, position_hint, tuple(hunk_lines)), index


def _parse_unified_diff(lines: list[str]) -> tuple[_FilePatch, ...]:
    file_patches: list[_FilePatch] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line or line.startswith(("diff --git ", "index ", "new file mode ", "deleted file mode ")):
            index += 1
            continue
        if line.startswith("GIT binary patch"):
            raise ValueError("apply_patch 不支持二进制补丁")
        if not line.startswith("--- "):
            raise ValueError(f"补丁第 {index + 1} 行应为 --- 文件头")

        old_path = _header_path(line[4:])
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise ValueError("每个 --- 文件头后必须紧跟 +++ 文件头")
        new_path = _header_path(lines[index][4:])
        index += 1
        if old_path is None and new_path is None:
            raise ValueError("补丁不能同时使用两个 /dev/null 文件头")

        hunks: list[_Hunk] = []
        while index < len(lines) and not lines[index].startswith("--- "):
            if lines[index].startswith("diff --git "):
                break
            hunk, index = _parse_unified_hunk(lines, index)
            hunks.append(hunk)
        if not hunks:
            raise ValueError("每个文件补丁至少需要一个 hunk")
        file_patches.append(_FilePatch(old_path, new_path, tuple(hunks)))

    if not file_patches:
        raise ValueError("补丁中未找到文件修改")
    return tuple(file_patches)


def _parse_unified_hunk(lines: list[str], index: int) -> tuple[_Hunk, int]:
    match = _HUNK_HEADER.fullmatch(lines[index])
    if match is None:
        raise ValueError(f"补丁第 {index + 1} 行的 hunk 头无效")
    # Unified diff uses line zero for a new file; its insertion point is still index zero.
    position_hint = max(int(match["old_start"]) - 1, 0)
    index += 1
    hunk_lines: list[tuple[str, str]] = []
    while index < len(lines):
        line = lines[index]
        if line.startswith(("@@ ", "--- ", "diff --git ")):
            break
        if line == r"\ No newline at end of file":
            index += 1
            continue
        if not line or line[0] not in {" ", "+", "-"}:
            raise ValueError(f"补丁第 {index + 1} 行不是有效 hunk 内容")
        hunk_lines.append((line[0], line[1:]))
        index += 1
    if not hunk_lines:
        raise ValueError("unified diff hunk 不能为空")
    # Line counts are a model-generated hint. The actual hunk text is the only safe source of truth.
    return _Hunk(None, False, position_hint, tuple(hunk_lines)), index


def _directive_path(line: str, prefix: str) -> str:
    path = line.removeprefix(prefix).strip()
    if not path:
        raise ValueError("补丁文件路径不能为空")
    return path


def _header_path(value: str) -> str | None:
    path = value.split("\t", maxsplit=1)[0]
    if path == "/dev/null":
        return None
    if path.startswith(("a/", "b/")):
        path = path[2:]
    if not path:
        raise ValueError("补丁文件路径不能为空")
    return path


def _plan_changes(file_patches: tuple[_FilePatch, ...], workspace: Path) -> tuple[_PlannedChange, ...]:
    """先基于同一份文件快照验证所有 hunk，避免失败补丁留下半截内容。"""

    planned: list[_PlannedChange] = []
    occupied_paths: set[Path] = set()
    for file_patch in file_patches:
        source_path = (
            _workspace_path(workspace, file_patch.old_path) if file_patch.old_path is not None else None
        )
        destination_path = (
            _workspace_path(workspace, file_patch.new_path) if file_patch.new_path is not None else None
        )
        targets = {path for path in (source_path, destination_path) if path is not None}
        if occupied_paths.intersection(targets):
            relative = file_patch.new_path or file_patch.old_path
            raise ValueError(f"同一文件不能在一个补丁中重复出现: {relative}")
        occupied_paths.update(targets)

        empty_placeholder = False
        if source_path is None:
            assert destination_path is not None
            if destination_path.exists():
                if not destination_path.is_file() or _read_text(destination_path):
                    raise ValueError(f"新增文件已存在: {file_patch.new_path}")
                empty_placeholder = True
            source_lines: list[str] = []
            newline = "\n"
            trailing_newline = True
        else:
            if not source_path.is_file():
                raise ValueError(f"待修改文件不存在或不是普通文件: {file_patch.old_path}")
            source = _read_text(source_path)
            source_lines = source.splitlines()
            newline = "\r\n" if "\r\n" in source else "\n"
            trailing_newline = source.endswith(("\n", "\r"))

        if (
            destination_path is not None
            and destination_path != source_path
            and destination_path.exists()
            and not empty_placeholder
        ):
            raise ValueError(f"移动或新增目标文件已存在: {file_patch.new_path}")

        result_lines = [] if file_patch.delete_only else _apply_hunks(
            source_lines, file_patch.hunks, file_patch.old_path or file_patch.new_path or ""
        )
        if destination_path is None:
            if result_lines:
                raise ValueError("删除文件的补丁必须移除全部内容")
            assert source_path is not None
            planned.append(_PlannedChange(source_path, None, "删除"))
            continue

        content = newline.join(result_lines)
        if result_lines and (trailing_newline or not source_lines):
            content += newline
        if source_path is not None and source_path != destination_path:
            planned.append(
                _PlannedChange(
                    destination_path,
                    content,
                    "",
                    f"移动 {source_path.relative_to(workspace)} -> {destination_path.relative_to(workspace)}",
                )
            )
            # Write the destination first so a failed write never destroys the source file.
            planned.append(_PlannedChange(source_path, None, ""))
        else:
            action = "修改" if source_path is not None or empty_placeholder else "新增"
            planned.append(_PlannedChange(destination_path, content, action))
    return tuple(planned)


def _workspace_path(workspace: Path, relative_path: str) -> Path:
    raw_path = Path(relative_path)
    if raw_path.is_absolute() or raw_path.drive:
        raise ValueError("补丁路径必须相对工作目录")
    if relative_path.replace("\\", "/").startswith(".git/"):
        raise ValueError("不允许通过 apply_patch 修改 .git 元数据")
    target = (workspace / raw_path).resolve()
    if not target.is_relative_to(workspace):
        raise ValueError("补丁路径超出工作目录")
    return target


def _read_text(path: Path) -> str:
    try:
        # Keep untouched CRLF lines intact instead of letting Python normalize them during the read.
        with path.open("r", encoding="utf-8", newline="") as file:
            return file.read()
    except UnicodeDecodeError as exc:
        raise ValueError(f"apply_patch 仅支持 UTF-8 文本文件: {path}") from exc


def _apply_hunks(source: list[str], hunks: tuple[_Hunk, ...], path: str) -> list[str]:
    result = list(source)
    for hunk in hunks:
        if hunk.raw_replacement:
            result = _apply_raw_replacement(result, hunk, path)
            continue
        hunk_lines = tuple(
            line
            for line in hunk.lines
            if not (
                hunk.end_of_file
                and line[0] in {"+", "-"}
                and _normalise_line(line[1]) == "*** End of File"
            )
        )
        hunk_lines = _remove_redundant_markdown_addition(hunk_lines)
        expected = [line for kind, line in hunk_lines if kind != "+"]
        try:
            start = _find_hunk_start(result, expected, hunk, path)
        except ValueError:
            repaired = _repair_redundant_replacement(hunk_lines) or _repair_redundant_anchor(
                hunk.anchor, hunk_lines
            ) or _repair_stuck_anchor_name(
                hunk.anchor, hunk_lines
            ) or _repair_markdown_deletion(
                hunk_lines
            ) or _repair_markdown_context(
                hunk_lines
            ) or _repair_anchor_as_replacement(hunk.anchor, hunk_lines)
            if repaired is None:
                raise
            hunk_lines = repaired
            expected = [line for kind, line in hunk_lines if kind != "+"]
            start = _find_hunk_start(result, expected, hunk, path)
        hunk_lines = _strip_diff_separator_if_exact(result, hunk_lines, start)
        expected = [line for kind, line in hunk_lines if kind != "+"]
        replacement = [line for kind, line in hunk_lines if kind != "-"]
        result[start : start + len(expected)] = replacement
    return result


def _strip_diff_separator_if_exact(
    source: list[str], lines: tuple[tuple[str, str], ...], start: int
) -> tuple[tuple[str, str], ...]:
    """Remove a visual separator space only when the source proves it is not content."""

    if not lines or not all(text.startswith(" ") for _, text in lines):
        return lines
    expected = [text for kind, text in lines if kind != "+"]
    adjusted = tuple((kind, text[1:]) for kind, text in lines)
    adjusted_expected = [text for kind, text in adjusted if kind != "+"]
    if not _matches_at(source, expected, start, 0) and _matches_at(
        source, adjusted_expected, start, 0
    ):
        return adjusted
    return lines


def _repair_redundant_replacement(
    lines: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...] | None:
    """Recover a repeated old line only after the literal hunk failed to match."""

    if (
        len(lines) == 3
        and lines[0][0] == " "
        and lines[1][0] == "-"
        and lines[2][0] in {" ", "+"}
        and _normalise_line(lines[0][1]) == _normalise_line(lines[1][1])
    ):
        return (lines[1], ("+", lines[2][1]))
    for deletion_index, (kind, deleted) in enumerate(lines):
        if kind != "-":
            continue
        for old_index in range(deletion_index - 1):
            old = lines[old_index]
            new = lines[old_index + 1]
            if (
                old[0] == new[0] == " "
                and _normalise_line(old[1]) == _normalise_line(deleted)
                and bool(_assignment_key(old[1]))
                and _assignment_key(old[1]) == _assignment_key(new[1])
                and _normalise_line(old[1]) != _normalise_line(new[1])
            ):
                repaired = list(lines)
                repaired[old_index] = ("-", old[1])
                repaired[old_index + 1] = ("+", new[1])
                del repaired[deletion_index]
                if (
                    deletion_index < len(repaired)
                    and repaired[deletion_index][0] == " "
                    and _normalise_line(repaired[deletion_index][1])
                    == _normalise_line(new[1])
                ):
                    del repaired[deletion_index]
                return tuple(repaired)
    return None


def _repair_anchor_as_replacement(
    anchor: str | None, lines: tuple[tuple[str, str], ...]
) -> tuple[tuple[str, str], ...] | None:
    if (
        anchor is not None
        and len(lines) == 2
        and lines[0][0] == " "
        and lines[1][0] == "-"
        and _normalise_line(lines[0][1]) == _normalise_line(lines[1][1])
        and bool(_assignment_key(anchor))
        and _assignment_key(anchor) == _assignment_key(lines[0][1])
        and _normalise_line(anchor) != _normalise_line(lines[0][1])
    ):
        return (("-", lines[0][1]), ("+", anchor))
    return None


def _assignment_key(line: str) -> str:
    key, separator, _ = line.partition("=")
    return key.strip() if separator else ""


def _repair_redundant_anchor(
    anchor: str | None, lines: tuple[tuple[str, str], ...]
) -> tuple[tuple[str, str], ...] | None:
    """Turn a repeated Markdown anchor into an insertion after that anchor."""

    if (
        anchor is not None
        and anchor.startswith("-")
        and len(lines) >= 3
        and lines[0][0] == "-"
        and lines[1][0] == "+"
        and all(kind == "+" for kind, _ in lines[2:])
        and _normalise_line("-" + lines[0][1]) == _normalise_line(anchor)
        and _normalise_line(lines[1][1])
        in {_normalise_line(anchor), _normalise_line(lines[0][1])}
    ):
        return tuple(
            (kind, text[1:] if text.startswith(" ") else text) for kind, text in lines[2:]
        )
    return None


def _repair_stuck_anchor_name(
    anchor: str | None, lines: tuple[tuple[str, str], ...]
) -> tuple[tuple[str, str], ...] | None:
    if anchor is None or not lines or lines[0][0] != "-":
        return None
    match = re.match(r"(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)", anchor)
    if match is None or not lines[0][1].startswith(match[1]):
        return None
    return (("-", lines[0][1][len(match[1]) :]), *lines[1:])


def _repair_markdown_deletion(
    lines: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...] | None:
    deletions = [index for index, (kind, _) in enumerate(lines) if kind == "-"]
    if len(deletions) != 1 or not any(
        kind == "+" and text.lstrip().startswith("- ") for kind, text in lines
    ):
        return None
    deletion_index = deletions[0]
    deleted = lines[deletion_index][1]
    if not deleted.startswith(" ") or deleted.startswith("  "):
        return None

    repaired: list[tuple[str, str]] = []
    for index, (kind, text) in enumerate(lines):
        if index == deletion_index:
            repaired.append((" ", "-" + text))
            continue
        if (
            kind == "+"
            and index == deletion_index + 1
            and _normalise_line(text) == _normalise_line(deleted)
        ):
            continue
        repaired.append((kind, text[1:] if kind == "+" and text.startswith(" - ") else text))
    return tuple(repaired)


def _repair_markdown_context(
    lines: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...] | None:
    if not any(kind == "+" and text.lstrip().startswith("- ") for kind, text in lines):
        return None
    repaired = tuple(
        (kind, text[1:] if kind == " " and text.startswith("-- ") else text)
        for kind, text in lines
    )
    return repaired if repaired != lines else None


def _remove_redundant_markdown_addition(
    lines: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    for index in range(len(lines) - 2):
        context, repeated, following = lines[index : index + 3]
        if (
            context[0] == " "
            and context[1].startswith("- ")
            and repeated[0] == "+"
            and following[0] == "+"
            and _normalise_line(context[1]) == _normalise_line(repeated[1])
        ):
            return (*lines[: index + 1], *lines[index + 2 :])
    return lines


def _apply_raw_replacement(source: list[str], hunk: _Hunk, path: str) -> list[str]:
    replacement = [line for _, line in hunk.lines]
    if not source:
        return replacement

    assert hunk.anchor is not None
    if not replacement or not any(_lines_match(replacement[0], hunk.anchor, level) for level in range(4)):
        raise ValueError(f"{path} 的裸内容必须从 @@ 锚点开始")
    for level in range(4):
        candidates = [
            index for index, line in enumerate(source) if _lines_match(line, hunk.anchor, level)
        ]
        if candidates:
            start = _select_match(candidates, None, path)
            return [*source[:start], *replacement]
    raise ValueError(f"{path} 的 hunk 不匹配：未找到 {hunk.anchor!r}")


def _find_hunk_start(source: list[str], expected: list[str], hunk: _Hunk, path: str) -> int:
    for level in range(4):
        candidates = _matching_positions(source, expected, hunk, level)
        if candidates:
            return _select_match(candidates, hunk.position_hint, path)
    expected_preview = "<文件结尾>" if not expected else repr(expected[0])
    raise ValueError(f"{path} 的 hunk 不匹配：未找到 {expected_preview}")


def _matching_positions(source: list[str], expected: list[str], hunk: _Hunk, level: int) -> list[int]:
    if hunk.end_of_file:
        start = len(source) - len(expected)
        return [start] if start >= 0 and _matches_at(source, expected, start, level) else []

    if hunk.anchor is not None:
        anchors = [
            index
            for index, line in enumerate(source)
            if _lines_match(line, hunk.anchor, level)
        ]
        anchored = [
            index + 1
            for index in anchors
            if _matches_at(source, expected, index + 1, level)
        ]
        if anchored:
            return anchored
        # The hunk body is stronger evidence than a model-generated label. Falling back
        # remains safe because ambiguous body matches are rejected by _select_match.

    if not expected:
        if not source:
            return [0]
        if hunk.position_hint is None:
            return []
        return [hunk.position_hint] if 0 <= hunk.position_hint <= len(source) else []

    return [
        index
        for index in range(len(source) - len(expected) + 1)
        if _matches_at(source, expected, index, level)
    ]


def _matches_at(source: list[str], expected: list[str], start: int, level: int) -> bool:
    return start + len(expected) <= len(source) and all(
        _lines_match(actual, wanted, level)
        for actual, wanted in zip(source[start : start + len(expected)], expected, strict=True)
    )


def _lines_match(actual: str, expected: str, level: int) -> bool:
    if level == 0:
        return actual == expected
    if level == 1:
        return actual.rstrip() == expected.rstrip()
    if level == 2:
        return actual.strip() == expected.strip()
    actual_normalised = _normalise_line(actual)
    expected_normalised = _normalise_line(expected)
    if actual_normalised == expected_normalised:
        return True
    if any(character in actual + expected for character in _TYPOGRAPHIC_DASHES):
        return re.sub(r"-+", "-", actual_normalised) == re.sub(r"-+", "-", expected_normalised)
    return False


def _normalise_line(line: str) -> str:
    return unicodedata.normalize("NFKC", line).translate(_TYPOGRAPHIC_CHARACTERS).strip()


def _select_match(candidates: list[int], position_hint: int | None, path: str) -> int:
    if len(candidates) == 1:
        return candidates[0]
    if position_hint is not None:
        return min(candidates, key=lambda candidate: (abs(candidate - position_hint), candidate))
    raise ValueError(f"{path} 的 hunk 上下文不唯一，请提供更多上下文")


def _write_change(change: _PlannedChange) -> None:
    if change.content is None:
        change.path.unlink()
        return

    change.path.parent.mkdir(parents=True, exist_ok=True)
    temporary = change.path.with_name(f".{change.path.name}.apply-patch-{uuid4().hex}")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as file:
            file.write(change.content)
        os.replace(temporary, change.path)
    finally:
        if temporary.exists():
            temporary.unlink()
