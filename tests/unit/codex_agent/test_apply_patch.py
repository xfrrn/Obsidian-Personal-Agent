"""apply_patch 的容错匹配、文件操作和工作目录边界验证。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.config.settings import Settings
from agent.core.handle import AgentHandle
from agent.core.loop import create_session
from agent.tools.handlers.apply_patch import ApplyPatchTool


class ApplyPatchToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_numeric_hunk_inside_codex_wrapper_and_end_marker_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = workspace / "config.py"
            target.write_text("TIMEOUT = 30\nDEBUG = False\n", encoding="utf-8")

            await ApplyPatchTool(_settings(workspace)).run(
                {
                    "patch": "*** Begin Patch\n"
                    "*** Update File: config.py\n"
                    "@@ -1,2 +1,2 @@\n"
                    "-TIMEOUT = 30\n"
                    "+TIMEOUT = 45\n"
                    " END OF PATCH ***"
                }
            )
            await ApplyPatchTool(_settings(workspace)).run(
                {
                    "patch": "*** Begin Patch\n"
                    "*** Update File: config.py\n"
                    "@@ timeout setting\n"
                    "-TIMEOUT = 45\n"
                    "+TIMEOUT = 50\n"
                    "*** End Patch"
                }
            )

            self.assertEqual(target.read_text(encoding="utf-8"), "TIMEOUT = 50\nDEBUG = False\n")
            await ApplyPatchTool(_settings(workspace)).run(
                {
                    "patch": "*** Begin Patch\n"
                    "*** Update File: config.py\n"
                    "@@\n"
                    " TIMEOUT = 50\n"
                    " TIMEOUT = 60\n"
                    " DEBUG = False\n"
                    "-TIMEOUT = 50\n"
                    "*** End Patch"
                }
            )
            self.assertEqual(target.read_text(encoding="utf-8"), "TIMEOUT = 60\nDEBUG = False\n")

    async def test_recovers_unmarked_numeric_replacement_and_eof_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            config = workspace / "config.py"
            changelog = workspace / "CHANGELOG.md"
            bullets = workspace / "bullets.md"
            mixed = workspace / "mixed.md"
            empty = workspace / "empty.txt"
            eof_only = workspace / "eof-only.md"
            eof_before = workspace / "eof-before.md"
            config.write_text("TIMEOUT = 30\nDEBUG = False\n", encoding="utf-8")
            changelog.write_text("# Changes\n", encoding="utf-8")
            bullets.write_text("- initial\n", encoding="utf-8")
            mixed.write_text("# Changes\n\n- initial\n", encoding="utf-8")
            empty.write_text("", encoding="utf-8")
            eof_only.write_text("- initial\n", encoding="utf-8")
            eof_before.write_text("- initial\n", encoding="utf-8")
            tool = ApplyPatchTool(_settings(workspace))

            await tool.run(
                {
                    "patch": "*** Begin Patch\n"
                    "*** Update File: config.py\n"
                    "@@ -1,1 +1,1 @@\n"
                    " TIMEOUT = 30\n"
                    " TIMEOUT = 45\n"
                    "*** Update File: CHANGELOG.md\n"
                    "@@ *** End of File:\n"
                    "+- fixed parser\n"
                    "*** End Patch"
                }
            )

            self.assertEqual(config.read_text(encoding="utf-8"), "TIMEOUT = 45\nDEBUG = False\n")
            self.assertEqual(changelog.read_text(encoding="utf-8"), "# Changes\n- fixed parser\n")
            await tool.run(
                {
                    "patch": "*** Begin Patch\n"
                    "*** Update File: bullets.md\n"
                    "@@ - initial\n"
                    "- initial\n"
                    "+- initial\n"
                    "+- fixed parser\n"
                    "*** End Patch"
                }
            )
            self.assertEqual(bullets.read_text(encoding="utf-8"), "- initial\n- fixed parser\n")
            await tool.run(
                {
                    "patch": "*** Begin Patch\n"
                    "*** Update File: mixed.md\n"
                    "@@\n"
                    "# Changes\n"
                    " \n"
                    " - initial\n"
                    "+- initial\n"
                    "+- fixed parser\n"
                    "*** Update File: empty.txt\n"
                    "@@ <end of file>\n"
                    "+created\n"
                    "*** End Patch"
                }
            )
            self.assertEqual(mixed.read_text(encoding="utf-8"), "# Changes\n\n- initial\n- fixed parser\n")
            self.assertEqual(empty.read_text(encoding="utf-8"), "created\n")
            await tool.run(
                {
                    "patch": "*** Begin Patch\n"
                    "*** Update File: eof-only.md\n"
                    "@@\n"
                    "- initial\n"
                    "+ initial\n"
                    "+ - fixed parser\n"
                    "*** End of File"
                }
            )
            self.assertEqual(eof_only.read_text(encoding="utf-8"), "- initial\n- fixed parser\n")
            await tool.run(
                {
                    "patch": "*** Begin Patch\n"
                    "*** Update File: eof-before.md\n"
                    "*** End of File\n"
                    "+- fixed parser\n"
                    "*** End Patch"
                }
            )
            self.assertEqual(eof_before.read_text(encoding="utf-8"), "- initial\n- fixed parser\n")

    async def test_recovers_top_level_move_and_redundant_old_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = workspace / "features.ini"
            target.write_text("[alpha]\nenabled = false\n\n[beta]\nenabled = false\n", encoding="utf-8")

            await ApplyPatchTool(_settings(workspace)).run(
                {
                    "patch": "*** Begin Patch\n"
                    "*** Move to: features.ini -> archive/features.ini\n"
                    "@@ [beta]\n"
                    " enabled = false\n"
                    "-enabled = false\n"
                    " enabled = true\n"
                    "*** End Patch"
                }
            )

            self.assertFalse(target.exists())
            self.assertEqual(
                (workspace / "archive" / "features.ini").read_text(encoding="utf-8"),
                "[alpha]\nenabled = false\n\n[beta]\nenabled = true\n",
            )

    async def test_removes_visual_diff_separator_without_damaging_indentation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            message = workspace / "message.py"
            code = workspace / "code.py"
            stuck = workspace / "stuck.py"
            dashes = workspace / "dashes.py"
            message.write_text("message = “hello”—ready  \n", encoding="utf-8")
            code.write_text("def value():\n    return 1\n", encoding="utf-8")
            stuck.write_text('def first():\n    return "old"\n', encoding="utf-8")
            dashes.write_text('message = “hello”—ready  \n', encoding="utf-8")

            await ApplyPatchTool(_settings(workspace)).run(
                {
                    "patch": "*** Begin Patch\n"
                    "*** Update File: message.py\n"
                    "@@\n"
                    "- message = “hello”—ready  \n"
                    "+ message = “goodbye”—ready  \n"
                    "*** Update File: code.py\n"
                    "@@ def value():\n"
                    "-    return 1\n"
                    "+    return 2\n"
                    "*** Update File: stuck.py\n"
                    "@@ def first():\n"
                    '-first    return "old"\n'
                    '+    return "new"\n'
                    "*** Update File: dashes.py\n"
                    "@@\n"
                    '-message = "hello"--ready  \n'
                    '+message = "goodbye"-ready\n'
                    "*** End Patch"
                }
            )

            self.assertEqual(message.read_text(encoding="utf-8"), "message = “goodbye”—ready  \n")
            self.assertEqual(code.read_text(encoding="utf-8"), "def value():\n    return 2\n")
            self.assertEqual(stuck.read_text(encoding="utf-8"), 'def first():\n    return "new"\n')
            self.assertEqual(dashes.read_text(encoding="utf-8"), 'message = "goodbye"-ready\n')

    async def test_applies_codex_hunk_through_heredoc_and_is_registered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = workspace / "notes.txt"
            target.write_text('def greeting():\n    return “hello”  \n', encoding="utf-8")
            settings = _settings(workspace)
            tool = ApplyPatchTool(settings)

            result = await tool.run(
                {
                    "patch": "apply_patch <<'PATCH'\n"
                    "*** Begin Patch\n"
                    "*** Update File: notes.txt\n"
                    "@@ def greeting():\n"
                    "-    return \"hello\"\n"
                    "+    return \"goodbye\"\n"
                    "*** End Patch\n"
                    "PATCH"
                }
            )
            session = create_session(settings, AgentHandle(), client=object())

            self.assertEqual(target.read_text(encoding="utf-8"), 'def greeting():\n    return "goodbye"\n')
            self.assertIn("修改 notes.txt", result)
            self.assertIn("apply_patch", [handler.spec.name for handler in session.tools.handlers()])

    async def test_supports_add_move_delete_and_end_of_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "notes.txt").write_text("alpha\n", encoding="utf-8")
            tool = ApplyPatchTool(_settings(workspace))

            await tool.run(
                {
                    "patch": "*** Begin Patch\n"
                    "*** Add File: nested/new.txt\n"
                    "+first\n"
                    "+second\n"
                    "*** Update File: notes.txt\n"
                    "@@\n"
                    " alpha\n"
                    "+omega\n"
                    "*** End of File\n"
                    "*** Move to: moved.txt\n"
                    "*** End Patch"
                }
            )
            await tool.run({"patch": "*** Begin Patch\n*** Delete File: nested/new.txt\n*** End Patch"})

            self.assertFalse((workspace / "notes.txt").exists())
            self.assertEqual((workspace / "moved.txt").read_text(encoding="utf-8"), "alpha\nomega\n")
            self.assertFalse((workspace / "nested" / "new.txt").exists())

            (workspace / "move-only.txt").write_text("unchanged\n", encoding="utf-8")
            await tool.run(
                {
                    "patch": "*** Begin Patch\n"
                    "*** Move to: move-only.txt -> archive/move-only.txt\n"
                    "@@\n"
                    "*** End Patch"
                }
            )
            self.assertFalse((workspace / "move-only.txt").exists())
            self.assertEqual(
                (workspace / "archive" / "move-only.txt").read_text(encoding="utf-8"),
                "unchanged\n",
            )

    async def test_accepts_raw_content_for_new_or_empty_files_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "empty.html").write_text("", encoding="utf-8")
            (workspace / "empty-update.html").write_text("", encoding="utf-8")
            (workspace / "existing.html").write_text("<old>\n", encoding="utf-8")
            tool = ApplyPatchTool(_settings(workspace))

            await tool.run(
                {
                    "patch": "*** Begin Patch\n"
                    "*** Add File: empty.html\n"
                    "<!DOCTYPE html>\n"
                    "<html></html>\n"
                    "*** End Patch"
                }
            )
            await tool.run(
                {
                    "patch": "*** Begin Patch\n"
                    "*** Update File: empty-update.html\n"
                    "@@ <!DOCTYPE html>\n"
                    "<!DOCTYPE html>\n"
                    "<html><body>created</body></html>\n"
                    "*** End Patch"
                }
            )
            await tool.run(
                {
                    "patch": "*** Begin Patch\n"
                    "*** Update File: empty.html\n"
                    "@@ <!DOCTYPE html>\n"
                    "<!DOCTYPE html>\n"
                    "<html><body>updated</body></html>\n"
                    "*** End Patch"
                }
            )
            with self.assertRaisesRegex(ValueError, "裸内容必须从 @@ 锚点开始"):
                await tool.run(
                    {
                        "patch": "*** Begin Patch\n"
                        "*** Update File: existing.html\n"
                        "@@ <old>\n"
                        "<new>\n"
                        "*** End Patch"
                    }
                )

            self.assertEqual(
                (workspace / "empty.html").read_text(encoding="utf-8"),
                "<!DOCTYPE html>\n<html><body>updated</body></html>\n",
            )
            self.assertEqual(
                (workspace / "empty-update.html").read_text(encoding="utf-8"),
                "<!DOCTYPE html>\n<html><body>created</body></html>\n",
            )
            self.assertEqual((workspace / "existing.html").read_text(encoding="utf-8"), "<old>\n")

    async def test_accepts_unified_diff_with_bad_counts_and_rejects_invalid_patch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = workspace / "notes.txt"
            other_target = workspace / "other.txt"
            target.write_text("alpha\n", encoding="utf-8")
            other_target.write_text("other\n", encoding="utf-8")
            tool = ApplyPatchTool(_settings(workspace))

            await tool.run(
                {"patch": "--- a/notes.txt\n+++ b/notes.txt\n@@ -9,4 +9,3 @@\n alpha\n+gamma\n"}
            )
            await tool.run(
                {"patch": "--- /dev/null\n+++ b/legacy.txt\n@@ -0,0 +1 @@\n+legacy\n"}
            )
            with self.assertRaisesRegex(ValueError, "超出工作目录"):
                await tool.run(
                    {"patch": "--- /dev/null\n+++ b/../outside.txt\n@@ -0,0 +1 @@\n+blocked\n"}
                )
            with self.assertRaisesRegex(ValueError, "hunk 不匹配"):
                await tool.run(
                    {
                        "patch": "*** Begin Patch\n"
                        "*** Update File: notes.txt\n"
                        "@@\n"
                        "-alpha\n"
                        "+blocked\n"
                        "*** Update File: other.txt\n"
                        "@@\n"
                        "-missing\n"
                        "+also-blocked\n"
                        "*** End Patch"
                    }
                )

            self.assertEqual(target.read_text(encoding="utf-8"), "alpha\ngamma\n")
            self.assertEqual(other_target.read_text(encoding="utf-8"), "other\n")
            self.assertEqual((workspace / "legacy.txt").read_text(encoding="utf-8"), "legacy\n")


def _settings(workspace: Path) -> Settings:
    return Settings(
        api_key=None,
        model="test-model",
        base_url="http://unused",
        system_prompt="test",
        workspace=workspace,
        shell_enabled=False,
        request_timeout_seconds=1,
    )
