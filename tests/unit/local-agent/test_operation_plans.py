from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [
    str(ROOT),
    str(ROOT / "packages"),
    str(ROOT / "packages" / "agent-core"),
    str(ROOT / "apps" / "local-agent" / "src"),
]

from infrastructure.operation_plans import (  # noqa: E402
    ExecutionMode,
    ExecutionPolicy,
    FilesystemOperationPlanExecutor,
    OperationManager,
    PersistentOperationPlanStore,
    SimpleOperationPlanner,
)


def _manager(root: Path, mode: ExecutionMode) -> OperationManager:
    data = root / ".obsidian-agent-data"
    policy = ExecutionPolicy(mode)
    store = PersistentOperationPlanStore(data / "state.sqlite3")
    planner = SimpleOperationPlanner(root, policy)
    executor = FilesystemOperationPlanExecutor(root, store, data / "audit.jsonl")
    return OperationManager(planner, store, executor, policy)


def test_execution_modes() -> None:
    safe = ("note.create.inbox",)
    assert ExecutionPolicy(ExecutionMode.CONFIRM_ALL).requires_confirmation(
        risk="low", capabilities=safe, source="interactive", affected_files=1
    )
    assert not ExecutionPolicy(ExecutionMode.RISK_BASED).requires_confirmation(
        risk="low", capabilities=safe, source="interactive", affected_files=1
    )
    unattended = ExecutionPolicy(ExecutionMode.UNATTENDED)
    assert unattended.requires_confirmation(
        risk="low", capabilities=safe, source="interactive", affected_files=1
    )
    assert not unattended.requires_confirmation(
        risk="low", capabilities=safe, source="automation", affected_files=1
    )
    assert unattended.requires_confirmation(
        risk="medium", capabilities=safe, source="automation", affected_files=1
    )


def test_dynamic_risk_and_protected_paths(tmp_path: Path) -> None:
    (tmp_path / ".obsidian").mkdir()
    for index in range(5):
        (tmp_path / f"Note-{index}.md").write_text("note\n", encoding="utf-8")
    manager = _manager(tmp_path, ExecutionMode.RISK_BASED)

    async def run() -> None:
        plan = await manager.stage({
            "operations": [
                {"type": "create-task", "path": f"Note-{index}.md", "title": f"task {index}"}
                for index in range(5)
            ],
            "context": {"allowedPaths": [f"Note-{index}.md" for index in range(5)]},
        })
        assert plan["risk"] == "high"
        assert plan["requiresConfirmation"]
        try:
            await manager.stage({
                "operations": [{"type": "create-note", "path": ".OBSIDIAN/unsafe.md", "content": "x"}],
            })
        except ValueError as error:
            assert "unsafe note path" in str(error)
        else:
            raise AssertionError("protected paths must be rejected")

    asyncio.run(run())


def test_persistent_execute_and_user_rollback(tmp_path: Path) -> None:
    (tmp_path / ".obsidian").mkdir()
    manager = _manager(tmp_path, ExecutionMode.RISK_BASED)

    async def run() -> None:
        plan = await manager.stage({
            "summary": "记录想法",
            "operations": [{"type": "create-note", "path": "00-Inbox/Idea.md", "content": "hello\n"}],
            "context": {"source": "interactive"},
        })
        assert plan["risk"] == "low"
        assert not plan["requiresConfirmation"]

        reloaded = PersistentOperationPlanStore(tmp_path / ".obsidian-agent-data" / "state.sqlite3")
        assert (await reloaded.get(plan["id"]))["status"] == "pending"

        result = await manager.execute(plan["id"])
        assert result["status"] == "succeeded"
        assert (tmp_path / "00-Inbox" / "Idea.md").read_text(encoding="utf-8") == "hello\n"

        await manager.rollback(plan["id"], trusted_system=True)
        assert not (tmp_path / "00-Inbox" / "Idea.md").exists()
        assert (await manager.store.get(plan["id"]))["status"] == "rolled_back"

    asyncio.run(run())
    audit = (tmp_path / ".obsidian-agent-data" / "audit.jsonl").read_text(encoding="utf-8")
    assert '"status":"succeeded"' in audit
    assert '"status":"user-rolled-back"' in audit


def test_all_file_operations_and_conflict_checks(tmp_path: Path) -> None:
    (tmp_path / ".obsidian").mkdir()
    source = tmp_path / "Project.md"
    original = "---\nstatus: draft\n中文字段: 保留\ntags:\n  - old\n---\n\nAlpha\n"
    source.write_text(original, encoding="utf-8")
    manager = _manager(tmp_path, ExecutionMode.CONFIRM_ALL)

    async def run() -> None:
        plan = await manager.stage({
            "summary": "整理项目",
            "operations": [
                {"type": "update-metadata", "path": "Project.md", "set": {"status": "active"}, "addTags": ["agent"]},
                {"type": "update-note", "path": "Project.md", "oldText": "Alpha", "newText": "Beta"},
                {"type": "create-task", "path": "Project.md", "title": "ship"},
                {"type": "move-note", "path": "Project.md", "targetPath": "01-Projects/Project.md"},
            ],
            "context": {"source": "interactive", "allowedPaths": ["Project.md"]},
        })
        assert plan["risk"] == "medium"
        assert plan["requiresConfirmation"]
        assert isinstance(plan.get("confirmationToken"), str)

        await manager.execute(plan["id"], confirmation_token=plan["confirmationToken"])
        moved = tmp_path / "01-Projects" / "Project.md"
        content = moved.read_text(encoding="utf-8")
        assert 'status: "active"' in content
        assert "中文字段: 保留" in content
        assert 'tags: ["old", "agent"]' in content
        assert "Beta" in content and "- [ ] ship" in content

        moved.write_text(content + "user edit\n", encoding="utf-8")
        try:
            await manager.rollback(plan["id"], trusted_system=True)
        except ValueError as error:
            assert "rollback conflict" in str(error)
        else:
            raise AssertionError("rollback must reject user changes")
        assert moved.exists()

    asyncio.run(run())


def test_execution_failure_rolls_back_completed_steps(tmp_path: Path) -> None:
    (tmp_path / ".obsidian").mkdir()
    source = tmp_path / "Note.md"
    source.write_text("before\n", encoding="utf-8")
    manager = _manager(tmp_path, ExecutionMode.CONFIRM_ALL)

    async def run() -> None:
        plan = await manager.stage({
            "operations": [
                {"type": "update-note", "path": "Note.md", "oldText": "before", "newText": "after"},
                {"type": "move-note", "path": "Note.md", "targetPath": "Moved.md"},
            ],
            "context": {"allowedPaths": ["Note.md"]},
        })
        (tmp_path / "Moved.md").write_text("collision\n", encoding="utf-8")
        try:
            await manager.execute(plan["id"], confirmation_token=plan["confirmationToken"])
        except ValueError as error:
            assert "move target already exists" in str(error)
        else:
            raise AssertionError("execution should fail")
        assert source.read_text(encoding="utf-8") == "before\n"
        assert (await manager.store.get(plan["id"]))["status"] == "rolled_back"

    asyncio.run(run())


def test_preview_conflict_invalidates_plan(tmp_path: Path) -> None:
    (tmp_path / ".obsidian").mkdir()
    note = tmp_path / "Note.md"
    note.write_text("before\n", encoding="utf-8")
    manager = _manager(tmp_path, ExecutionMode.CONFIRM_ALL)

    async def run() -> None:
        plan = await manager.stage({
            "operations": [
                {"type": "update-note", "path": "Note.md", "oldText": "before", "newText": "after"},
            ],
            "context": {"allowedPaths": ["Note.md"]},
        })
        note.write_text("user edit\n", encoding="utf-8")
        try:
            await manager.execute(plan["id"], confirmation_token=plan["confirmationToken"])
        except ValueError as error:
            assert "changed after preview" in str(error)
        else:
            raise AssertionError("execution must reject a stale preview")
        assert (await manager.store.get(plan["id"]))["status"] == "invalidated"
        assert note.read_text(encoding="utf-8") == "user edit\n"

    asyncio.run(run())


def test_trash_note_and_empty_folder_operations(tmp_path: Path) -> None:
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / "Note.md").write_text("keep me\n", encoding="utf-8")
    (tmp_path / "OldEmpty").mkdir()
    manager = _manager(tmp_path, ExecutionMode.CONFIRM_ALL)

    async def run() -> None:
        plan = await manager.stage({
            "operations": [
                {"type": "trash-note", "path": "Note.md"},
                {"type": "create-folder", "path": "NewEmpty"},
                {"type": "delete-folder", "path": "OldEmpty"},
            ],
            "context": {"allowedPaths": ["Note.md", "OldEmpty"]},
        })
        assert plan["risk"] == "high"
        assert plan["requiresConfirmation"]

        await manager.execute(plan["id"], confirmation_token=plan["confirmationToken"])
        assert not (tmp_path / "Note.md").exists()
        assert (tmp_path / ".trash" / "Note.md").read_text(encoding="utf-8") == "keep me\n"
        assert (tmp_path / "NewEmpty").is_dir()
        assert not (tmp_path / "OldEmpty").exists()

        await manager.rollback(plan["id"], trusted_system=True)
        assert (tmp_path / "Note.md").read_text(encoding="utf-8") == "keep me\n"
        assert not (tmp_path / ".trash" / "Note.md").exists()
        assert not (tmp_path / "NewEmpty").exists()
        assert (tmp_path / "OldEmpty").is_dir()

    asyncio.run(run())


def test_delete_folder_rejects_non_empty_directory(tmp_path: Path) -> None:
    (tmp_path / ".obsidian").mkdir()
    folder = tmp_path / "Folder"
    folder.mkdir()
    (folder / "Note.md").write_text("content\n", encoding="utf-8")
    manager = _manager(tmp_path, ExecutionMode.CONFIRM_ALL)

    async def run() -> None:
        try:
            await manager.stage({
                "operations": [{"type": "delete-folder", "path": "Folder"}],
                "context": {"allowedPaths": ["Folder"]},
            })
        except ValueError as error:
            assert "folder is not empty" in str(error)
        else:
            raise AssertionError("non-empty folders must not be deleted")

    asyncio.run(run())
