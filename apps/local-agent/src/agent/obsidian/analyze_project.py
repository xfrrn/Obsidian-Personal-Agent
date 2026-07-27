"""Analyze one project from its notes and tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Mapping

from .ports import NoteRepository, TaskRepository


REQUIRED_DOCUMENTS = {
    "项目说明": ("说明", "概览", "readme"),
    "需求": ("需求", "requirement"),
    "设计": ("设计", "design", "架构"),
    "进度或任务": ("进度", "任务", "task", "开发记录"),
}


@dataclass(frozen=True)
class AnalyzeProjectUseCase:
    notes: NoteRepository
    tasks: TaskRepository

    async def execute(self, input_data: Mapping[str, Any]) -> dict[str, Any]:
        project = _project_name(input_data)
        matches = tuple(await self.notes.search("", limit=500, filters={"project": project, "sort": "modified_desc"}))
        notes = tuple([await self.notes.get(str(item["path"])) for item in matches])
        tasks = tuple(await self.tasks.list("", limit=500, filters={"project": project}))
        open_tasks = tuple(task for task in tasks if not task.get("completed"))
        titles = " ".join(
            f"{note.get('title', '')} {note.get('metadata', {}).get('type', '')}".casefold()
            for note in notes
        )
        missing = tuple(
            name for name, markers in REQUIRED_DOCUMENTS.items()
            if not any(marker in titles for marker in markers)
        )
        stale = tuple(
            str(note["path"])
            for note in notes
            if (datetime.now().astimezone() - datetime.fromisoformat(str(note["modifiedAt"]))).days > 30
        )
        statuses = {
            str(note.get("metadata", {}).get("status"))
            for note in notes
            if isinstance(note.get("metadata"), Mapping) and note.get("metadata", {}).get("status")
        }
        message = "\n".join((
            f"项目分析：{project}",
            f"- 笔记：{len(notes)} 篇",
            f"- 未完成任务：{len(open_tasks)} 条",
            f"- 状态：{'、'.join(sorted(statuses)) or '未声明'}",
            f"- 建议检查的缺失文档：{'、'.join(missing) or '无'}",
            f"- 超过 30 天未更新：{len(stale)} 篇",
        ))
        return {
            "project": project,
            "noteCount": len(notes),
            "notes": tuple({"path": note["path"], "title": note["title"], "modifiedAt": note["modifiedAt"]} for note in notes),
            "taskCount": len(tasks),
            "openTasks": open_tasks,
            "statuses": tuple(sorted(statuses)),
            "missingDocuments": missing,
            "staleNotes": stale,
            "citations": tuple({"path": str(note["path"])} for note in notes[:50]),
            "message": message,
        }


def _project_name(input_data: Mapping[str, Any]) -> str:
    for key in ("project", "projectName"):
        value = input_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    active = input_data.get("activeFilePath")
    if isinstance(active, str):
        parts = PurePosixPath(active.replace("\\", "/")).parts
        if len(parts) > 2 and parts[0] == "01-Projects":
            return parts[1]
    raise ValueError("project name is required")
