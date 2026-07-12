"""Deterministic note and vault analysis use cases."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
import re
from typing import Any, Mapping, Sequence

from domain.notes.repositories import NoteRepository


@dataclass(frozen=True)
class InspectNoteUseCase:
    notes: NoteRepository

    async def execute(self, input_data: Mapping[str, Any]) -> dict[str, Any]:
        path = await _resolve_target(self.notes, input_data)
        note = await self.notes.get(path)
        catalog = tuple(await self.notes.all())
        rules = tuple(await self.notes.rules())
        report = _inspect(note, catalog, rules)
        return {**report, "citations": ({"path": path},), "message": _inspection_message(report)}


@dataclass(frozen=True)
class CheckVaultHealthUseCase:
    notes: NoteRepository

    async def execute(self, input_data: Mapping[str, Any]) -> dict[str, Any]:
        catalog = tuple(await self.notes.all(limit=_limit(input_data.get("limit", 2000), 1, 5000)))
        rules = tuple(await self.notes.rules())
        issues: list[Mapping[str, Any]] = []
        incoming = Counter(
            target
            for note in catalog
            for link in note.get("links", ())
            if (target := _resolve_link(str(link), str(note["path"]), catalog))
        )
        title_paths: dict[str, list[str]] = {}
        for note in catalog:
            report = _inspect(note, catalog, rules)
            issues.extend(report["issues"])
            title_paths.setdefault(str(note["title"]).casefold(), []).append(str(note["path"]))
            if not note.get("links") and not incoming[str(note["path"])] and not str(note["path"]).startswith("04-Daily/"):
                issues.append(_issue(note, "orphan-note", "info", "笔记没有链接到其他笔记，也没有被引用"))
        for paths in title_paths.values():
            if len(paths) > 1:
                for path in paths:
                    issues.append({"path": path, "code": "duplicate-title", "severity": "warning", "message": f"同名笔记：{', '.join(paths)}"})
        counts = Counter(str(issue["severity"]) for issue in issues)
        visible = tuple(issues[:500])
        return {
            "noteCount": len(catalog),
            "issueCount": len(issues),
            "severityCounts": dict(counts),
            "issues": visible,
            "citations": tuple({"path": path} for path in dict.fromkeys(str(item["path"]) for item in visible[:50])),
            "truncated": len(visible) < len(issues),
            "message": _health_message(len(catalog), issues, counts),
        }


@dataclass(frozen=True)
class ListTagsUseCase:
    notes: NoteRepository

    async def execute(self, input_data: Mapping[str, Any]) -> dict[str, Any]:
        catalog = tuple(await self.notes.all())
        counts = Counter(str(tag) for note in catalog for tag in note.get("tags", ()))
        aliases: dict[str, tuple[str, ...]] = {}
        deprecated: set[str] = set()
        for rule in await self.notes.rules():
            if rule.get("kind") != "tag_alias" or not isinstance(rule.get("canonical"), str):
                continue
            canonical = str(rule["canonical"])
            aliases[canonical] = tuple(str(value) for value in rule.get("aliases", ()) if isinstance(value, str))
            if rule.get("deprecated") is True:
                deprecated.add(canonical)
        tags = tuple(
            {
                "tag": tag,
                "count": count,
                "category": tag.split("/", 1)[0] if "/" in tag else "uncategorized",
                "aliases": aliases.get(tag, ()),
                "deprecated": tag in deprecated,
            }
            for tag, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        )
        return {
            "count": len(tags),
            "tags": tags,
            "message": "标签统计：\n" + ("\n".join(f"- #{item['tag']}：{item['count']}" for item in tags[:30]) or "- 没有标签"),
        }


@dataclass(frozen=True)
class FindRelatedNotesUseCase:
    notes: NoteRepository

    async def execute(self, input_data: Mapping[str, Any]) -> dict[str, Any]:
        path = await _resolve_target(self.notes, input_data)
        catalog = tuple(await self.notes.all())
        source = _catalog_note(path, catalog)
        source_terms = _terms(str(source.get("content", "")))
        source_links = {
            target
            for link in source.get("links", ())
            if (target := _resolve_link(str(link), path, catalog))
        }
        results: list[Mapping[str, Any]] = []
        for note in catalog:
            candidate_path = str(note["path"])
            if candidate_path == path:
                continue
            reasons: list[str] = []
            score = 0.0
            if candidate_path in source_links:
                score += 10
                reasons.append("当前笔记直接链接")
            if any(_resolve_link(str(link), candidate_path, catalog) == path for link in note.get("links", ())):
                score += 8
                reasons.append("反向链接")
            shared_tags = sorted(set(source.get("tags", ())) & set(note.get("tags", ())))
            if shared_tags:
                score += min(len(shared_tags) * 3, 9)
                reasons.append("共同标签：" + "、".join(shared_tags))
            if source.get("project") and source.get("project") == note.get("project"):
                score += 4
                reasons.append("同一项目")
            overlap = _jaccard(source_terms, _terms(str(note.get("content", ""))))
            if overlap >= 0.15:
                score += overlap * 5
                reasons.append(f"内容词项重合 {overlap:.0%}")
            if score:
                results.append({
                    "path": candidate_path,
                    "title": note["title"],
                    "score": round(score, 3),
                    "source": "deterministic",
                    "reasons": tuple(reasons),
                })
        results.sort(key=lambda item: (-float(item["score"]), str(item["path"])))
        visible = tuple(results[: _limit(input_data.get("limit", 10), 1, 50)])
        return {
            "path": path,
            "count": len(visible),
            "results": visible,
            "citations": tuple({"path": str(item["path"])} for item in visible),
            "message": "相关笔记：\n" + ("\n".join(f"- {item['path']}（{'; '.join(item['reasons'])}）" for item in visible) or "- 没有找到可靠关联"),
        }


@dataclass(frozen=True)
class FindDuplicatesUseCase:
    notes: NoteRepository

    async def execute(self, input_data: Mapping[str, Any]) -> dict[str, Any]:
        catalog = tuple(await self.notes.all(limit=_limit(input_data.get("scan_limit", 1000), 2, 2000)))
        threshold = _threshold(input_data.get("threshold", 0.85))
        normalized = {str(note["path"]): _normalized_content(str(note.get("content", ""))) for note in catalog}
        terms = {path: _terms(content) for path, content in normalized.items()}
        results: list[Mapping[str, Any]] = []
        # ponytail: O(n²) is acceptable for a personal Vault; replace with an index above 2,000 scanned notes.
        for index, left in enumerate(catalog):
            left_path = str(left["path"])
            for right in catalog[index + 1 :]:
                right_path = str(right["path"])
                if not normalized[left_path] or not normalized[right_path]:
                    continue
                exact = normalized[left_path] == normalized[right_path]
                similarity = 1.0 if exact else _jaccard(terms[left_path], terms[right_path])
                if exact or similarity >= threshold:
                    results.append({
                        "leftPath": left_path,
                        "rightPath": right_path,
                        "similarity": round(similarity, 3),
                        "exact": exact,
                        "source": "exact" if exact else "lexical-similarity",
                    })
        results.sort(key=lambda item: (-float(item["similarity"]), str(item["leftPath"])))
        visible = tuple(results[: _limit(input_data.get("limit", 100), 1, 500)])
        return {
            "count": len(visible),
            "results": visible,
            "citations": tuple(
                {"path": path}
                for path in dict.fromkeys(
                    str(value)
                    for item in visible
                    for value in (item["leftPath"], item["rightPath"])
                )
            ),
            "message": "重复或相似笔记：\n" + ("\n".join(f"- {item['leftPath']} ↔ {item['rightPath']}（{float(item['similarity']):.0%}）" for item in visible) or "- 未发现"),
        }


@dataclass(frozen=True)
class ListRulesUseCase:
    notes: NoteRepository

    async def execute(self, _input_data: Mapping[str, Any]) -> dict[str, Any]:
        rules = tuple(await self.notes.rules())
        return {
            "count": len(rules),
            "rules": rules,
            "message": "当前规则：\n" + "\n".join(f"- {rule.get('id')}：{rule.get('description', rule.get('kind'))}" for rule in rules),
        }


@dataclass(frozen=True)
class EvaluateRulesUseCase:
    notes: NoteRepository

    async def execute(self, input_data: Mapping[str, Any]) -> dict[str, Any]:
        catalog = tuple(await self.notes.all())
        rules = tuple(await self.notes.rules())
        path = _optional_path(input_data)
        prefix = str(input_data.get("path_prefix") or "").replace("\\", "/").strip("/")
        selected = tuple(
            note
            for note in catalog
            if (not path or note["path"] == path)
            and (not prefix or note["path"] == prefix or str(note["path"]).startswith(prefix + "/"))
        )
        if path and not selected:
            raise FileNotFoundError(path)
        issues = tuple(issue for note in selected for issue in _rule_issues(note, rules))
        return {
            "noteCount": len(selected),
            "issueCount": len(issues),
            "issues": issues,
            "citations": tuple({"path": path} for path in dict.fromkeys(str(item["path"]) for item in issues[:100])),
            "message": "规则试运行：\n" + ("\n".join(f"- {item['path']}：{item['message']}" for item in issues[:100]) or "- 未发现违规"),
        }


@dataclass(frozen=True)
class ExtractTaskCandidatesUseCase:
    notes: NoteRepository

    async def execute(self, input_data: Mapping[str, Any]) -> dict[str, Any]:
        path = await _resolve_target(self.notes, input_data)
        note = await self.notes.get(path)
        candidates: list[Mapping[str, Any]] = []
        markers = re.compile(r"(?:TODO|待办|下一步|需要|应该|计划|记得|后续)", re.IGNORECASE)
        for line_no, line in enumerate(str(note.get("content", "")).splitlines(), start=1):
            clean = line.strip().lstrip("-+* ")
            if not clean or re.match(r"^\[[ xX]\]", clean) or clean.startswith("#") or not markers.search(clean):
                continue
            if len(clean) <= 200:
                candidates.append({"path": path, "line": line_no, "title": clean})
        visible = tuple(candidates[: _limit(input_data.get("limit", 30), 1, 100)])
        return {
            "path": path,
            "count": len(visible),
            "candidates": visible,
            "citations": ({"path": path},),
            "message": "潜在任务：\n" + ("\n".join(f"- {item['title']}（第 {item['line']} 行）" for item in visible) or "- 未发现"),
        }


def _inspect(
    note: Mapping[str, Any],
    catalog: Sequence[Mapping[str, Any]],
    rules: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    issues = list(_rule_issues(note, rules))
    if note.get("frontmatterError"):
        issues.append(_issue(note, "invalid-frontmatter", "error", str(note["frontmatterError"])))
    if str(note.get("title", "")).casefold() in {"untitled", "未命名", "无标题"}:
        issues.append(_issue(note, "placeholder-title", "warning", "文件名仍是占位标题"))
    if not note.get("metadata"):
        issues.append(_issue(note, "missing-frontmatter", "info", "笔记没有 Frontmatter"))
    if not note.get("tags"):
        issues.append(_issue(note, "missing-tags", "info", "笔记没有标签"))
    metadata = note.get("metadata", {})
    if (
        isinstance(metadata, Mapping)
        and str(metadata.get("status", "")).casefold() in {"done", "completed", "complete", "已完成"}
        and str(note["path"]).startswith("01-Projects/")
    ):
        issues.append(_issue(note, "completed-project-not-archived", "warning", "项目已完成但仍位于 Projects"))
    headings = tuple(note.get("headings", ()))
    for heading, count in Counter(headings).items():
        if count > 1:
            issues.append(_issue(note, "duplicate-heading", "warning", f"标题重复：{heading}"))
    broken = tuple(
        str(link)
        for link in note.get("links", ())
        if not _resolve_link(str(link), str(note["path"]), catalog)
    )
    for link in broken:
        issues.append(_issue(note, "broken-link", "warning", f"链接目标不存在：{link}"))
    classification = _classification(note)
    return {
        "path": note["path"],
        "classification": classification,
        "recommendedFolder": _recommended_folder(note, classification),
        "brokenLinks": broken,
        "issueCount": len(issues),
        "issues": tuple(issues),
    }


def _rule_issues(note: Mapping[str, Any], rules: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    issues: list[Mapping[str, Any]] = []
    path = str(note["path"])
    metadata = note.get("metadata", {}) if isinstance(note.get("metadata"), Mapping) else {}
    for rule in rules:
        prefix = str(rule.get("pathPrefix") or "").strip("/")
        if prefix and path != prefix and not path.startswith(prefix + "/"):
            continue
        kind = rule.get("kind")
        severity = str(rule.get("severity") or "warning")
        if kind == "required_metadata":
            missing = [str(field) for field in rule.get("fields", ()) if str(field) not in metadata or metadata[str(field)] in (None, "", [])]
            if missing:
                issues.append(_issue(note, str(rule.get("id")), severity, "缺少属性：" + "、".join(missing)))
        elif kind == "max_age_days":
            age = (datetime.now().astimezone() - datetime.fromisoformat(str(note["modifiedAt"]))).days
            if age > int(rule.get("days", 0)):
                issues.append(_issue(note, str(rule.get("id")), severity, f"已 {age} 天未整理"))
        elif kind == "filename_pattern" and isinstance(rule.get("pattern"), str):
            if not re.fullmatch(str(rule["pattern"]), str(note["title"])):
                issues.append(_issue(note, str(rule.get("id")), severity, "文件名不符合规则"))
    return tuple(issues)


def _issue(note: Mapping[str, Any], code: str, severity: str, message: str) -> Mapping[str, Any]:
    return {"path": note["path"], "code": code, "severity": severity, "message": message}


def _classification(note: Mapping[str, Any]) -> str:
    metadata = note.get("metadata", {})
    if isinstance(metadata, Mapping) and isinstance(metadata.get("type"), str) and metadata["type"].strip():
        return str(metadata["type"]).strip()
    top = str(note["path"]).split("/", 1)[0]
    return {
        "00-Inbox": "inbox",
        "01-Projects": "project-note",
        "02-Areas": "area-note",
        "03-Learning": "learning-note",
        "04-Daily": "daily-note",
        "05-Problems": "problem-note",
        "06-Resources": "resource-note",
        "07-Archive": "archive-note",
        "08-Tasks": "task-note",
    }.get(top, "note")


def _recommended_folder(note: Mapping[str, Any], classification: str) -> str | None:
    if classification == "project-note" and note.get("project"):
        return f"01-Projects/{note['project']}"
    return {
        "inbox": "00-Inbox",
        "daily-note": "04-Daily",
        "task-note": "08-Tasks",
        "resource-note": "06-Resources",
        "archive-note": "07-Archive",
    }.get(classification)


def _resolve_link(link: str, source_path: str, catalog: Sequence[Mapping[str, Any]]) -> str | None:
    clean = link.replace("\\", "/").strip().removesuffix(".md")
    source_parent = str(PurePosixPath(source_path).parent)
    candidates = {clean.casefold(), f"{clean}.md".casefold()}
    if source_parent != ".":
        candidates.update({f"{source_parent}/{clean}".casefold(), f"{source_parent}/{clean}.md".casefold()})
    by_stem = [str(note["path"]) for note in catalog if str(note["title"]).casefold() == PurePosixPath(clean).name.casefold()]
    for note in catalog:
        path = str(note["path"])
        if path.casefold() in candidates or path.removesuffix(".md").casefold() in candidates:
            return path
    return by_stem[0] if by_stem else None


async def _resolve_target(notes: NoteRepository, input_data: Mapping[str, Any]) -> str:
    path = _optional_path(input_data)
    if path:
        return path
    name = input_data.get("noteName")
    if isinstance(name, str) and name.strip():
        matches = tuple(await notes.search(name.strip(), limit=5))
        exact = [item for item in matches if str(item.get("title", "")).casefold() == name.strip().removesuffix(".md").casefold()]
        if len(exact) == 1:
            return str(exact[0]["path"])
        if len(matches) == 1:
            return str(matches[0]["path"])
    raise ValueError("note path is required")


def _optional_path(input_data: Mapping[str, Any]) -> str | None:
    for key in ("path", "notePath", "activeFilePath"):
        value = input_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _catalog_note(path: str, catalog: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    for note in catalog:
        if note["path"] == path:
            return note
    raise FileNotFoundError(path)


def _terms(content: str) -> frozenset[str]:
    values: list[str] = []
    for value in re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]+", _body(content).casefold()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", value) and len(value) > 2:
            values.extend(value[index : index + 2] for index in range(len(value) - 1))
        else:
            values.append(value)
    return frozenset(values)


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    return len(left & right) / len(left | right) if left and right else 0.0


def _normalized_content(content: str) -> str:
    return " ".join(_body(content).casefold().split())


def _body(content: str) -> str:
    lines = content.splitlines()
    if lines and lines[0].strip() == "---":
        try:
            return "\n".join(lines[lines.index("---", 1) + 1 :])
        except ValueError:
            pass
    return content


def _threshold(value: Any) -> float:
    try:
        return max(0.5, min(float(value), 1.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("threshold must be a number") from exc


def _limit(value: Any, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc


def _inspection_message(report: Mapping[str, Any]) -> str:
    issues = report["issues"]
    return (
        f"笔记检查：{report['path']}\n"
        f"- 类型：{report['classification']}\n"
        f"- 建议目录：{report.get('recommendedFolder') or '保持当前位置'}\n"
        f"- 问题数：{report['issueCount']}\n"
        + ("\n".join(f"- [{item['severity']}] {item['message']}" for item in issues) if issues else "- 未发现问题")
    )


def _health_message(note_count: int, issues: Sequence[Mapping[str, Any]], counts: Counter[str]) -> str:
    lines = [
        f"知识库健康检查：{note_count} 篇笔记，{len(issues)} 个问题。",
        f"- error: {counts['error']}，warning: {counts['warning']}，info: {counts['info']}",
    ]
    lines.extend(f"- {item['path']}：{item['message']}" for item in issues[:50])
    return "\n".join(lines)
