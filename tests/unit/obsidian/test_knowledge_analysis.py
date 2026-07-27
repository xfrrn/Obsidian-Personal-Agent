from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "local-agent" / "src"))

from agent.obsidian.analyze_notes import (  # noqa: E402
    CheckVaultHealthUseCase,
    EvaluateRulesUseCase,
    ExtractTaskCandidatesUseCase,
    FindDuplicatesUseCase,
    FindRelatedNotesUseCase,
    InspectNoteUseCase,
    ListRulesUseCase,
    ListTagsUseCase,
)
from agent.obsidian.analyze_project import AnalyzeProjectUseCase  # noqa: E402
from agent.obsidian.list_tasks import ListTasksUseCase  # noqa: E402
from agent.obsidian.read_note import ReadNoteUseCase  # noqa: E402
from agent.obsidian.search_notes import SearchNotesUseCase  # noqa: E402
from agent.obsidian.vault import LocalDirectoryVaultRepository  # noqa: E402


def test_p1_p2_knowledge_tools(tmp_path: Path) -> None:
    (tmp_path / ".obsidian").mkdir()
    inbox = tmp_path / "00-Inbox" / "Old.md"
    overview = tmp_path / "01-Projects" / "Agent" / "项目说明.md"
    design = tmp_path / "01-Projects" / "Agent" / "Design.md"
    copy = tmp_path / "01-Projects" / "Agent" / "Design Copy.md"
    tasks_path = tmp_path / "08-Tasks" / "Tasks.md"
    for path in (inbox, overview, design, copy, tasks_path):
        path.parent.mkdir(parents=True, exist_ok=True)

    inbox.write_text("---\ntags: [inbox]\n---\n\n[[Missing]]\n", encoding="utf-8")
    old = time.time() - 10 * 24 * 60 * 60
    os.utime(inbox, (old, old))
    overview.write_text(
        "---\nproject: Agent\nstatus: active\ntags: [agent, project/core]\n---\n\n# 项目说明\n\n[[Design]]\n下一步：补测试\n",
        encoding="utf-8",
    )
    duplicate_content = "---\nproject: Agent\nstatus: active\ntags: [agent]\n---\n\n设计正文完全相同。\n"
    design.write_text(duplicate_content, encoding="utf-8")
    copy.write_text(duplicate_content, encoding="utf-8")
    due = date.today().isoformat()
    tasks_path.write_text(
        f"---\nproject: Agent\nstatus: active\n---\n\n- [ ] 完成 P2 ⏫ 📅 {due}\n",
        encoding="utf-8",
    )
    repository = LocalDirectoryVaultRepository(tmp_path)

    async def run() -> None:
        search = await SearchNotesUseCase(repository).execute({
            "query": "设计",
            "project": "Agent",
            "tags": ["agent"],
            "path_prefix": "01-Projects/Agent",
        })
        assert {item["path"] for item in search["results"]} == {
            "01-Projects/Agent/Design.md",
            "01-Projects/Agent/Design Copy.md",
        }
        recent = await SearchNotesUseCase(repository).execute({
            "query": "",
            "modified_after": (datetime.now().astimezone() - timedelta(days=1)).isoformat(),
        })
        assert "00-Inbox/Old.md" not in {item["path"] for item in recent["results"]}

        batch = await ReadNoteUseCase(repository).execute({"paths": ["00-Inbox/Old.md", "01-Projects/Agent/项目说明.md"]})
        assert batch["count"] == 2
        assert all("metadata" in note for note in batch["notes"])

        tasks = await ListTasksUseCase(repository).execute({
            "status": "open",
            "due_on": due,
            "priority": "high",
            "project": "Agent",
        })
        assert tasks["count"] == 1
        assert tasks["tasks"][0]["title"] == "完成 P2"

        inspection = await InspectNoteUseCase(repository).execute({"path": "00-Inbox/Old.md"})
        assert inspection["classification"] == "inbox"
        assert {issue["code"] for issue in inspection["issues"]} >= {"inbox-age", "broken-link"}

        project = await AnalyzeProjectUseCase(repository, repository).execute({"project": "Agent"})
        assert project["noteCount"] >= 4
        assert len(project["openTasks"]) == 1

        health = await CheckVaultHealthUseCase(repository).execute({})
        assert health["noteCount"] == 5
        assert health["issueCount"] > 0

        tags = await ListTagsUseCase(repository).execute({})
        assert next(item for item in tags["tags"] if item["tag"] == "agent")["count"] == 3

        related = await FindRelatedNotesUseCase(repository).execute({"path": "01-Projects/Agent/项目说明.md"})
        assert related["results"][0]["path"] == "01-Projects/Agent/Design.md"

        duplicates = await FindDuplicatesUseCase(repository).execute({})
        assert any(item["exact"] for item in duplicates["results"])

        rules = await ListRulesUseCase(repository).execute({})
        assert rules["count"] >= 3
        evaluated = await EvaluateRulesUseCase(repository).execute({"path": "00-Inbox/Old.md"})
        assert evaluated["issueCount"] >= 1

        candidates = await ExtractTaskCandidatesUseCase(repository).execute({"path": "01-Projects/Agent/项目说明.md"})
        assert candidates["count"] == 1

    asyncio.run(run())
