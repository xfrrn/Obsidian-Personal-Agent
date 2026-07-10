"""Local directory vault adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

TASK_RE = re.compile(r"^\s*[-+*]\s+\[([ xX])\]\s+(.+?)\s*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class LocalDirectoryVaultRepository:
    """Read notes and tasks from a local Markdown vault."""

    root: Path

    async def get(self, path: str) -> Mapping[str, Any]:
        file = self._resolve_note(path)
        content = file.read_text(encoding="utf-8")
        return {
            "path": self._relative(file),
            "title": file.stem,
            "headings": self._headings(content),
            "content": content,
        }

    async def search(self, query: str, *, limit: int = 10) -> Sequence[Mapping[str, Any]]:
        terms = _search_terms(query)
        results: list[tuple[int, Mapping[str, Any]]] = []
        for file in self._markdown_files():
            content = file.read_text(encoding="utf-8", errors="ignore")
            rel = self._relative(file)
            haystack = f"{rel}\n{file.stem}\n{content}".casefold()
            score = sum(1 for term in terms if term in haystack)
            if terms and score == 0:
                continue
            results.append(
                (
                    score,
                    {
                        "path": rel,
                        "title": file.stem,
                        "score": score,
                        "excerpt": " ".join(content.split())[:500],
                    },
                )
            )
        results.sort(key=lambda item: (-item[0], str(item[1]["path"])))
        return tuple(item for _score, item in results[:limit])

    async def list(
        self,
        query: str = "",
        *,
        status: str | None = None,
        limit: int = 50,
        path: str | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        query_text = query.casefold().strip()
        want_completed = _completed_filter(status)
        tasks: list[Mapping[str, Any]] = []
        files = (self._resolve_note(path),) if path else self._markdown_files()
        for file in files:
            content = file.read_text(encoding="utf-8", errors="ignore")
            heading: str | None = None
            for line_no, line in enumerate(content.splitlines(), start=1):
                if heading_match := HEADING_RE.match(line):
                    heading = heading_match.group(2)
                    continue
                task_match = TASK_RE.match(line)
                if not task_match:
                    continue
                completed = task_match.group(1).casefold() == "x"
                title = task_match.group(2).strip()
                rel = self._relative(file)
                if want_completed is not None and completed is not want_completed:
                    continue
                if query_text and query_text not in f"{rel} {title} {heading or ''}".casefold():
                    continue
                tasks.append(
                    {
                        "path": rel,
                        "line": line_no,
                        "title": title,
                        "completed": completed,
                        "heading": heading,
                    }
                )
                if len(tasks) >= limit:
                    return tuple(tasks)
        return tuple(tasks)

    def _markdown_files(self) -> tuple[Path, ...]:
        root = self.root.resolve()
        return tuple(
            sorted(
                (
                    path
                    for path in root.rglob("*.md")
                    if ".obsidian" not in path.relative_to(root).parts
                ),
                key=lambda path: self._relative(path),
            )
        )

    def _resolve_note(self, path: str) -> Path:
        if not path or "://" in path or Path(path).is_absolute():
            raise ValueError(f"unsafe note path: {path}")
        clean = Path(path.replace("\\", "/"))
        if clean.suffix != ".md" or ".." in clean.parts or ".obsidian" in clean.parts:
            raise ValueError(f"unsafe note path: {path}")
        root = self.root.resolve()
        file = (root / clean).resolve()
        if root not in file.parents:
            raise ValueError(f"unsafe note path: {path}")
        if not file.exists() or not file.is_file():
            raise FileNotFoundError(path)
        return file

    def _relative(self, file: Path) -> str:
        return file.resolve().relative_to(self.root.resolve()).as_posix()

    def _headings(self, content: str) -> tuple[str, ...]:
        return tuple(match.group(2) for line in content.splitlines() if (match := HEADING_RE.match(line)))


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
