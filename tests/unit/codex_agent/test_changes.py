"""Vault 文件变更记录与选择性撤销。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.changes import ChangeConflictError, ChangeJournal


class ChangeJournalTest(unittest.IsolatedAsyncioTestCase):
    async def test_records_diff_and_undoes_selected_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "vault"
            workspace.mkdir()
            first = workspace / "first.md"
            deleted = workspace / "deleted.md"
            first.write_text("old\n", encoding="utf-8")
            deleted.write_text("restore me\n", encoding="utf-8")
            journal = ChangeJournal(workspace, Path(directory) / "sessions.db")

            await journal.begin_turn("session", 1)
            first.write_text("new\nsecond\n", encoding="utf-8")
            (workspace / "created.md").write_text("created\n", encoding="utf-8")
            deleted.unlink()
            summary = journal.finish_turn("session", 1)

            self.assertIsNotNone(summary)
            assert summary is not None
            self.assertEqual([file["path"] for file in summary["files"]], [
                "created.md", "deleted.md", "first.md"
            ])
            detail = journal.get("session", str(summary["id"]))
            first_change = next(file for file in detail["files"] if file["path"] == "first.md")
            self.assertIn("@@", first_change["diff"])
            self.assertIn("+second", first_change["diff"])

            journal.undo("session", str(summary["id"]), ["first.md", "created.md"])
            self.assertEqual(first.read_text(encoding="utf-8"), "old\n")
            self.assertFalse((workspace / "created.md").exists())
            self.assertFalse(deleted.exists())
            journal.undo("session", str(summary["id"]), ["deleted.md"])
            self.assertEqual(deleted.read_text(encoding="utf-8"), "restore me\n")

    async def test_refuses_to_overwrite_a_later_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "vault"
            workspace.mkdir()
            note = workspace / "note.md"
            note.write_text("before", encoding="utf-8")
            journal = ChangeJournal(workspace, Path(directory) / "sessions.db")
            await journal.begin_turn("session", 1)
            note.write_text("agent", encoding="utf-8")
            summary = journal.finish_turn("session", 1)
            assert summary is not None

            note.write_text("user", encoding="utf-8")
            with self.assertRaises(ChangeConflictError):
                journal.undo("session", str(summary["id"]), ["note.md"])
            self.assertEqual(note.read_text(encoding="utf-8"), "user")


if __name__ == "__main__":
    unittest.main()
