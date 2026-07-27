"""工作区 AGENTS.md 发现与优先级验证。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.config.loader import load_project_instructions


class ProjectInstructionsTest(unittest.TestCase):
    def test_layers_project_rules_from_root_to_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            workspace = root / "services" / "payments"
            workspace.mkdir(parents=True)
            _write(root / "AGENTS.md", "根目录规则")
            _write(root / "services" / "AGENTS.md", "服务规则")
            _write(workspace / "AGENTS.md", "不应被读取")
            _write(workspace / "AGENTS.override.md", "支付服务规则")

            instructions = load_project_instructions(workspace)

        self.assertLess(instructions.index("根目录规则"), instructions.index("服务规则"))
        self.assertLess(instructions.index("服务规则"), instructions.index("支付服务规则"))
        self.assertNotIn("不应被读取", instructions)

    def test_empty_override_falls_back_to_agents_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _write(workspace / "AGENTS.override.md", "\n")
            _write(workspace / "AGENTS.md", "后备规则")

            instructions = load_project_instructions(workspace)

        self.assertIn("后备规则", instructions)

    def test_claude_is_only_used_when_agents_files_are_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _write(workspace / "CLAUDE.md", "Claude 规则")

            instructions = load_project_instructions(workspace)
            _write(workspace / "AGENTS.md", "AGENTS 规则")
            agents_instructions = load_project_instructions(workspace)

        self.assertIn("Claude 规则", instructions)
        self.assertIn("AGENTS 规则", agents_instructions)
        self.assertNotIn("Claude 规则", agents_instructions)

    def test_non_git_workspace_does_not_load_parent_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "child"
            workspace.mkdir()
            _write(root / "AGENTS.md", "父目录规则")
            _write(workspace / "AGENTS.md", "当前目录规则")

            instructions = load_project_instructions(workspace)

        self.assertIn("当前目录规则", instructions)
        self.assertNotIn("父目录规则", instructions)

    def test_instruction_body_is_limited_to_32_kib(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _write(workspace / "AGENTS.md", "甲" * (32 * 1024))

            instructions = load_project_instructions(workspace)

        body = instructions.partition("### AGENTS.md\n")[2]
        self.assertEqual(body, "甲" * (32 * 1024 // 3))


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
