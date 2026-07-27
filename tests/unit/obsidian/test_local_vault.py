from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "local-agent" / "src"))

from agent.obsidian.vault import LocalDirectoryVaultRepository  # noqa: E402


def test_local_vault_supports_chinese_queries_and_current_file_tasks(tmp_path: Path) -> None:
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / "A.md").write_text("# 个人知识库\n\n+ [ ] 当前任务\n", encoding="utf-8")
    (tmp_path / "B.md").write_text("# 其他\n\n- [ ] 其他任务\n", encoding="utf-8")
    repository = LocalDirectoryVaultRepository(tmp_path)

    results = asyncio.run(repository.search("请搜索个人知识库笔记"))
    assert results[0]["path"] == "A.md"

    tasks = asyncio.run(repository.list(path="A.md"))
    assert [task["title"] for task in tasks] == ["当前任务"]
