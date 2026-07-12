"""Local Markdown vault adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

TASK_RE = re.compile(r"^\s*[-+*]\s+\[([ xX])\]\s+(.+?)\s*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
WIKI_LINK_RE = re.compile(r"!?\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)#]+\.md)(?:#[^)]*)?\)")
INLINE_TAG_RE = re.compile(r"(?<![\w/])#([\w\-/\u4e00-\u9fff]+)")
TASK_DATE_MARKERS = {
    "due": re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})"),
    "scheduled": re.compile(r"⏳\s*(\d{4}-\d{2}-\d{2})"),
    "start": re.compile(r"🛫\s*(\d{4}-\d{2}-\d{2})"),
    "created": re.compile(r"➕\s*(\d{4}-\d{2}-\d{2})"),
    "done": re.compile(r"✅\s*(\d{4}-\d{2}-\d{2})"),
}
PRIORITIES = (("highest", "🔺"), ("high", "⏫"), ("medium", "🔼"), ("low", "🔽"), ("lowest", "⏬"))
IGNORED_DIRS = frozenset({".obsidian", ".obsidian-agent-data", ".git", ".trash"})

DEFAULT_RULES: tuple[Mapping[str, Any], ...] = (
    {
        "id": "inbox-age",
        "description": "Inbox 笔记超过 7 天需要整理",
        "kind": "max_age_days",
        "pathPrefix": "00-Inbox/",
        "days": 7,
        "severity": "warning",
    },
    {
        "id": "project-metadata",
        "description": "项目笔记应包含 project 和 status 属性",
        "kind": "required_metadata",
        "pathPrefix": "01-Projects/",
        "fields": ["project", "status"],
        "severity": "warning",
    },
    {
        "id": "task-project",
        "description": "任务笔记应声明所属项目",
        "kind": "required_metadata",
        "pathPrefix": "08-Tasks/",
        "fields": ["project"],
        "severity": "info",
    },
)


@dataclass(frozen=True)
class LocalDirectoryVaultRepository:
    """Read notes, metadata, links and Tasks syntax from one local Vault."""

    root: Path

    async def get(self, path: str) -> Mapping[str, Any]:
        return self._note(self._resolve_note(path))

    async def all(self, *, limit: int = 5000) -> Sequence[Mapping[str, Any]]:
        return tuple(self._note(file) for file in self._markdown_files()[: _limit(limit, 1, 5000)])

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        filters: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        terms = _search_terms(query)
        filters = dict(filters or {})
        results: list[tuple[int, Mapping[str, Any]]] = []
        for file in self._markdown_files():
            note = self._note(file)
            if not _matches_note(note, filters):
                continue
            score, matched_fields = _note_score(note, terms)
            if terms and score == 0:
                continue
            results.append((score, _search_result(note, score, matched_fields)))
        sort = str(filters.get("sort") or "relevance")
        if sort == "modified_desc":
            results.sort(key=lambda item: (str(item[1]["modifiedAt"]), str(item[1]["path"])), reverse=True)
        elif sort == "modified_asc":
            results.sort(key=lambda item: (str(item[1]["modifiedAt"]), str(item[1]["path"])))
        elif sort == "path":
            results.sort(key=lambda item: str(item[1]["path"]))
        else:
            results.sort(key=lambda item: (-item[0], str(item[1]["path"])))
        return tuple(item for _score, item in results[: _limit(limit, 1, 100)])

    async def list(
        self,
        query: str = "",
        *,
        status: str | None = None,
        limit: int = 50,
        path: str | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        query_text = query.casefold().strip()
        want_completed = _completed_filter(status)
        filters = dict(filters or {})
        path_prefix = str(filters.get("path_prefix") or "").replace("\\", "/").strip("/")
        tasks: list[Mapping[str, Any]] = []
        files = (self._resolve_note(path),) if path else self._markdown_files()
        for file in files:
            note = self._note(file)
            rel = str(note["path"])
            if path_prefix and rel != path_prefix and not rel.startswith(path_prefix + "/"):
                continue
            heading: str | None = None
            for line_no, line in enumerate(str(note["content"]).splitlines(), start=1):
                if heading_match := HEADING_RE.match(line):
                    heading = heading_match.group(2)
                    continue
                task_match = TASK_RE.match(line)
                if not task_match:
                    continue
                task = _task(rel, line_no, heading, task_match, note)
                if want_completed is not None and task["completed"] is not want_completed:
                    continue
                if query_text and query_text not in f"{rel} {task['title']} {heading or ''}".casefold():
                    continue
                if not _matches_task(task, filters):
                    continue
                tasks.append(task)
        tasks.sort(key=lambda item: (str(item.get("due") or "9999-99-99"), str(item["path"]), int(item["line"])))
        return tuple(tasks[: _limit(limit, 1, 500)])

    async def rules(self) -> Sequence[Mapping[str, Any]]:
        path = self.root / ".obsidian-agent-data" / "rules.json"
        custom: list[Mapping[str, Any]] = []
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
                raise ValueError("rules.json must contain an array of objects")
            custom = value
        merged = {str(rule["id"]): dict(rule) for rule in DEFAULT_RULES}
        for rule in custom:
            rule_id = rule.get("id")
            if not isinstance(rule_id, str) or not rule_id.strip():
                raise ValueError("every rule needs an id")
            merged[rule_id] = dict(rule)
        return tuple(merged.values())

    def _note(self, file: Path) -> Mapping[str, Any]:
        content = file.read_text(encoding="utf-8", errors="replace")
        metadata, frontmatter_error = _frontmatter(content)
        stat = file.stat()
        tags = _tags(content, metadata)
        return {
            "path": self._relative(file),
            "title": file.stem,
            "headings": self._headings(content),
            "content": content,
            "metadata": metadata,
            "tags": tags,
            "links": _links(content),
            "project": _project(self._relative(file), metadata),
            "modifiedAt": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
            "createdAt": datetime.fromtimestamp(stat.st_ctime).astimezone().isoformat(),
            "size": stat.st_size,
            "frontmatterError": frontmatter_error,
        }

    def _markdown_files(self) -> tuple[Path, ...]:
        root = self.root.resolve()
        return tuple(sorted(
            (
                path
                for path in root.rglob("*.md")
                if not any(part.casefold() in IGNORED_DIRS for part in path.relative_to(root).parts)
            ),
            key=lambda path: self._relative(path),
        ))

    def _resolve_note(self, path: str) -> Path:
        if not path or "://" in path or Path(path).is_absolute():
            raise ValueError(f"unsafe note path: {path}")
        clean = Path(path.replace("\\", "/"))
        if clean.suffix.casefold() != ".md" or ".." in clean.parts or any(part.casefold() in IGNORED_DIRS for part in clean.parts):
            raise ValueError(f"unsafe note path: {path}")
        root = self.root.resolve()
        file = (root / clean).resolve()
        if root not in file.parents:
            raise ValueError(f"unsafe note path: {path}")
        if not file.is_file():
            raise FileNotFoundError(path)
        return file

    def _relative(self, file: Path) -> str:
        return file.resolve().relative_to(self.root.resolve()).as_posix()

    def _headings(self, content: str) -> tuple[str, ...]:
        return tuple(match.group(2) for line in content.splitlines() if (match := HEADING_RE.match(line)))


def _frontmatter(content: str) -> tuple[dict[str, Any], str | None]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, None
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, "Frontmatter 缺少结束分隔符"
    result: dict[str, Any] = {}
    current: str | None = None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")) and ":" in line:
            key, _, raw = line.partition(":")
            current = key.strip()
            if not current:
                return result, "Frontmatter 包含空字段名"
            result[current] = _yaml_scalar(raw.strip()) if raw.strip() else []
        elif current and line.lstrip().startswith("- "):
            value = _yaml_scalar(line.lstrip()[2:].strip())
            existing = result.setdefault(current, [])
            if isinstance(existing, list):
                existing.append(value)
        elif line.startswith((" ", "\t")):
            continue
        else:
            return result, f"无法解析 Frontmatter 行：{line}"
    return result, None


def _yaml_scalar(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = json.loads(value.replace("'", '"'))
            return parsed if isinstance(parsed, list) else value
        except json.JSONDecodeError:
            return [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
    clean = value.strip("'\"")
    if clean.casefold() in {"true", "false"}:
        return clean.casefold() == "true"
    return clean


def _tags(content: str, metadata: Mapping[str, Any]) -> tuple[str, ...]:
    result: list[str] = []
    raw = metadata.get("tags", ())
    values = raw if isinstance(raw, list) else [raw] if isinstance(raw, str) else []
    for value in (*values, *(match.group(1) for match in INLINE_TAG_RE.finditer(content))):
        tag = str(value).strip().lstrip("#")
        if tag and tag not in result:
            result.append(tag)
    return tuple(result)


def _links(content: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        [match.group(1).strip() for match in WIKI_LINK_RE.finditer(content)]
        + [match.group(1).strip() for match in MARKDOWN_LINK_RE.finditer(content)]
    ))


def _project(path: str, metadata: Mapping[str, Any]) -> str | None:
    value = metadata.get("project")
    if isinstance(value, str) and value.strip():
        return value.strip()
    parts = path.split("/")
    return parts[1] if len(parts) > 2 and parts[0] == "01-Projects" else None


def _matches_note(note: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    path = str(note["path"])
    prefix = str(filters.get("path_prefix") or filters.get("path") or "").replace("\\", "/").strip("/")
    if prefix and path != prefix and not path.startswith(prefix + "/"):
        return False
    wanted_tags = filters.get("tags", ())
    if isinstance(wanted_tags, str):
        wanted_tags = (wanted_tags,)
    actual_tags = {str(tag).casefold() for tag in note.get("tags", ())}
    if isinstance(wanted_tags, (list, tuple)) and any(str(tag).casefold().lstrip("#") not in actual_tags for tag in wanted_tags):
        return False
    project = filters.get("project")
    if isinstance(project, str) and project.strip() and str(note.get("project") or "").casefold() != project.strip().casefold():
        return False
    modified = datetime.fromisoformat(str(note["modifiedAt"])).timestamp()
    if filters.get("modified_after") and modified < _timestamp(filters["modified_after"]):
        return False
    if filters.get("modified_before") and modified > _timestamp(filters["modified_before"]):
        return False
    return True


def _note_score(note: Mapping[str, Any], terms: Sequence[str]) -> tuple[int, tuple[str, ...]]:
    fields = {
        "title": str(note["title"]).casefold(),
        "path": str(note["path"]).casefold(),
        "tags": " ".join(str(value) for value in note.get("tags", ())).casefold(),
        "project": str(note.get("project") or "").casefold(),
        "headings": " ".join(str(value) for value in note.get("headings", ())).casefold(),
        "content": str(note["content"]).casefold(),
    }
    weights = {"title": 6, "path": 5, "tags": 4, "project": 4, "headings": 3, "content": 1}
    matched = tuple(name for name, value in fields.items() if any(term in value for term in terms))
    score = sum(weights[name] for name, value in fields.items() for term in terms if term in value)
    return score, matched


def _search_result(note: Mapping[str, Any], score: int, matched_fields: Sequence[str]) -> Mapping[str, Any]:
    return {
        "path": note["path"],
        "title": note["title"],
        "score": score,
        "matchedFields": tuple(matched_fields),
        "excerpt": " ".join(str(note["content"]).split())[:500],
        "tags": note["tags"],
        "project": note["project"],
        "modifiedAt": note["modifiedAt"],
    }


def _task(
    path: str,
    line_no: int,
    heading: str | None,
    match: re.Match[str],
    note: Mapping[str, Any],
) -> dict[str, Any]:
    raw = match.group(2).strip()
    dates = {name: found.group(1) if (found := pattern.search(raw)) else None for name, pattern in TASK_DATE_MARKERS.items()}
    priority = next((name for name, marker in PRIORITIES if marker in raw), None)
    recurrence = (found.group(1).strip() if (found := re.search(r"🔁\s*([^📅⏳🛫➕✅🔺⏫🔼🔽⏬]+)", raw)) else None)
    title = raw
    for pattern in (*TASK_DATE_MARKERS.values(), re.compile(r"🔁\s*[^📅⏳🛫➕✅🔺⏫🔼🔽⏬]+")):
        title = pattern.sub("", title)
    for _name, marker in PRIORITIES:
        title = title.replace(marker, "")
    return {
        "path": path,
        "line": line_no,
        "title": " ".join(title.split()),
        "rawTitle": raw,
        "completed": match.group(1).casefold() == "x",
        "heading": heading,
        "project": _task_project(raw, note),
        "priority": priority,
        "recurrence": recurrence,
        **dates,
    }


def _matches_task(task: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    for key in ("priority", "project"):
        value = filters.get(key)
        if isinstance(value, str) and value.strip() and str(task.get(key) or "").casefold() != value.strip().casefold():
            return False
    due = task.get("due")
    if filters.get("due_on") and due != filters["due_on"]:
        return False
    if filters.get("due_after") and (not due or due < filters["due_after"]):
        return False
    if filters.get("due_before") and (not due or due >= filters["due_before"]):
        return False
    return True


def _task_project(raw: str, note: Mapping[str, Any]) -> str | None:
    if match := re.search(r"(?:project|项目)::\s*([^📅⏳🛫➕✅🔺⏫🔼🔽⏬#]+)", raw, re.IGNORECASE):
        return match.group(1).strip()
    if match := re.search(r"#project/([\w\-/\u4e00-\u9fff]+)", raw, re.IGNORECASE):
        return match.group(1).strip()
    return str(note["project"]) if note.get("project") else None


def _completed_filter(status: str | None) -> bool | None:
    if not status:
        return None
    value = status.casefold()
    if value in {"done", "completed", "complete", "closed", "已完成"}:
        return True
    if value in {"open", "todo", "pending", "未完成"}:
        return False
    return None


def _search_terms(query: str) -> list[str]:
    terms: list[str] = []
    for value in re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]+", query.casefold()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", value) and len(value) > 2:
            terms.extend(value[index : index + 2] for index in range(len(value) - 1))
        else:
            terms.append(value)
    return list(dict.fromkeys(terms))


def _timestamp(value: Any) -> float:
    if not isinstance(value, str):
        raise ValueError("date filters must be ISO strings")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _limit(value: Any, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
