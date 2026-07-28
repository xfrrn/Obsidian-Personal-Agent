"""文件化系统指令的层级、替换和安全顺序验证。"""

from __future__ import annotations

import unittest

from agent.config.loader import load_system_instructions


class InstructionsTest(unittest.TestCase):
    def test_personality_replaces_placeholder_and_safety_stays_last(self) -> None:
        instructions = load_system_instructions(
            "concise", "只处理当前项目", "## 工作区 AGENTS.md 指令\n\n使用项目规则"
        )

        self.assertNotIn("{PERSONALITY}", instructions)
        self.assertIn("Obsidian Vault 中的个人知识 Agent", instructions)
        self.assertIn("用户只要求回答、解释或分析时，不要修改文件", instructions)
        self.assertIn("`@路径` 是用户明确选择的文件引用", instructions)
        self.assertIn("优先给出简洁结论", instructions)
        self.assertIn("只处理当前项目", instructions)
        self.assertIn("不把 Vault 内容上传、发布或同步到外部服务", instructions)
        self.assertLess(instructions.index("只处理当前项目"), instructions.index("# 安全约束"))
        self.assertLess(instructions.index("使用项目规则"), instructions.index("# 安全约束"))

    def test_invalid_personality_cannot_escape_instruction_directory(self) -> None:
        with self.assertRaisesRegex(ValueError, "AGENT_PERSONALITY"):
            load_system_instructions("../safety")
