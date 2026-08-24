"""Skill 目录、显式 mention 与 Agent 提示词注入的端到端验证。"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agent.config.settings import Settings
from agent.core.handle import AgentHandle
from agent.core.loop import create_session, submission_loop
from agent.core.turn.public_events import PublicEventAdapter
from agent.llm.types import AssistantResponse, ToolCall
from agent.permissions import SandboxMode
from agent.protocol.event import EventKind
from agent.protocol.op import UserInput
from agent.skills.injection import build_skill_injections, collect_explicit_mentions
from agent.skills.invocation import detect_implicit_skill_invocations
from agent.skills.loader import Skill
from agent.skills.render import render_available_skills
from agent.skills.service import SkillsService
from agent.web.metrics import AgentMetrics


class RecordingClient:
    """记录模型实际看到的消息，确认 Skill 是上下文而非 Tool。"""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.tools: list[dict[str, Any]] = []

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AssistantResponse:
        self.messages = messages
        self.tools = tools
        return AssistantResponse("已按 Skill 处理")


class ScriptCallingClient(RecordingClient):
    def __init__(self, command: str) -> None:
        super().__init__()
        self.command = command
        self.calls = 0

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AssistantResponse:
        self.messages = messages
        self.tools = tools
        self.calls += 1
        if self.calls == 1:
            return AssistantResponse(
                None,
                (ToolCall("call-script", "exec_command", {"command": self.command}),),
            )
        return AssistantResponse("脚本已运行")


class SkillInjectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_is_visible_but_only_explicit_skill_body_is_injected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "vault"
            workspace.mkdir()
            session_db_path = root / "plugin-data" / "sessions.db"
            _write_skill(
                session_db_path.parent / "skills" / "code-review" / "SKILL.md",
                "code-review",
                "审查 Python 代码",
                "先列出严重问题，再给出最小修复建议。",
            )
            _write_skill(
                session_db_path.parent / "skills" / "secret-workflow" / "SKILL.md",
                "secret-workflow",
                "不应自动加载的工作流",
                "这段正文绝不能在未显式提及时进入提示词。",
            )
            settings = Settings(
                api_key=None,
                model="test-model",
                base_url="http://unused",
                system_prompt="test system",
                workspace=workspace,
                shell_enabled=False,
                request_timeout_seconds=1,
                session_db_path=session_db_path,
            )
            handle = AgentHandle()
            client = RecordingClient()
            session = create_session(settings, handle, client)
            events = PublicEventAdapter()
            handle.turn_events.subscribe(events)
            loop_task = asyncio.create_task(submission_loop(session, handle))
            handle.submit(UserInput("请用 $code-review 审查这段代码"))

            kinds: list[EventKind] = []
            while EventKind.TURN_FINISHED not in kinds:
                kinds.append((await asyncio.wait_for(events.receive(), timeout=1)).kind)
            handle.shutdown()
            await asyncio.wait_for(loop_task, timeout=1)

        system_context = "\n".join(
            message["content"] for message in client.messages if message["role"] == "system"
        )
        self.assertIn("$code-review: 审查 Python 代码", system_context)
        self.assertIn("$secret-workflow: 不应自动加载的工作流", system_context)
        self.assertIn("先列出严重问题", system_context)
        self.assertNotIn("这段正文绝不能", system_context)
        self.assertIn(
            f"<path>{session_db_path.parent / 'skills' / 'code-review'}</path>",
            system_context,
        )
        self.assertIn("相对路径以该文件所在目录为基准", system_context)
        self.assertIn("优先运行或修改已有脚本", system_context)
        self.assertIn(
            f"(file: {session_db_path.parent / 'skills' / 'secret-workflow' / 'SKILL.md'})",
            system_context,
        )
        self.assertEqual(
            [tool["function"]["name"] for tool in client.tools],
            [
                "apply_patch",
                "create_frontmatter",
                "current_time",
                "get_context_remaining",
                "mutate_task",
                "new_context_window",
                "obsidian_command",
                "query_tasks",
                "update_plan",
                "update_properties",
                "view_image",
            ],
        )

    async def test_agent_tracks_a_skill_script_without_explicit_mention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "vault"
            workspace.mkdir()
            session_db_path = root / "plugin-data" / "sessions.db"
            skill_file = session_db_path.parent / "skills" / "scripted" / "SKILL.md"
            _write_skill(skill_file, "scripted", "运行自带脚本", "运行 scripts/check.py。")
            script = skill_file.parent / "scripts" / "check.py"
            script.parent.mkdir()
            script.write_text("print('ok')\n", encoding="utf-8")
            client = ScriptCallingClient(f'"{sys.executable}" "{script}"')
            settings = Settings(
                api_key=None,
                model="test-model",
                base_url="http://unused",
                system_prompt="test system",
                workspace=workspace,
                shell_enabled=True,
                request_timeout_seconds=5,
                session_db_path=session_db_path,
                sandbox_mode=SandboxMode.DANGER_FULL_ACCESS,
            )
            handle = AgentHandle()
            session = create_session(settings, handle, client)
            metrics = AgentMetrics()
            handle.turn_events.subscribe(metrics.record)
            events = PublicEventAdapter()
            handle.turn_events.subscribe(events)
            loop_task = asyncio.create_task(submission_loop(session, handle))
            handle.submit(UserInput("运行项目自带的检查脚本"))

            kinds: list[EventKind] = []
            while EventKind.TURN_FINISHED not in kinds:
                kinds.append((await asyncio.wait_for(events.receive(), timeout=5)).kind)
            snapshot = metrics.snapshot()
            system_context = "\n".join(
                message["content"]
                for message in client.messages
                if message["role"] == "system"
            )
            handle.shutdown()
            await asyncio.wait_for(loop_task, timeout=1)

        self.assertIn("任务明显匹配下方描述", system_context)
        self.assertIn(f"(file: {skill_file})", system_context)
        self.assertEqual(snapshot["skills"]["explicit_invocations"], 0)
        self.assertEqual(snapshot["skills"]["implicit_invocations"], 1)
        self.assertEqual(snapshot["skills"]["by_name"], {"scripted": 1})


class SkillBehaviorTest(unittest.TestCase):
    def test_invalid_skill_is_skipped_without_hiding_valid_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "valid" / "SKILL.md"
            valid.parent.mkdir()
            valid.write_text(
                "\ufeff---\n"
                "name: valid\n"
                "description: |\n"
                "  Valid workflow: handles YAML metadata.\n"
                "  Use when compatibility matters.\n"
                "metadata:\n"
                "  author: example-org\n"
                '  version: "1.0"\n'
                "---\n"
                "Do the work.\n",
                encoding="utf-8",
            )
            invalid = root / "invalid" / "SKILL.md"
            invalid.parent.mkdir()
            invalid.write_text("invalid front matter", encoding="utf-8")

            with self.assertLogs("agent.skills.loader", level="WARNING"):
                skills = SkillsService(root).snapshot()

        self.assertEqual(tuple(skill.name for skill in skills), ("valid",))
        self.assertEqual(
            skills[0].description,
            "Valid workflow: handles YAML metadata.\nUse when compatibility matters.",
        )

    def test_bundled_installer_and_new_user_skill_are_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(
                api_key=None,
                model="test-model",
                base_url="http://unused",
                system_prompt="test system",
                workspace=root,
                shell_enabled=False,
                request_timeout_seconds=1,
                session_db_path=root / "state" / "sessions.db",
            )
            session = create_session(settings, AgentHandle(), RecordingClient())

            initial = session.skills_service.snapshot()
            installer = next(skill for skill in initial if skill.name == "skill-installer")
            self.assertTrue(
                (installer.path.parent / "scripts" / "install-skill-from-github.ps1").is_file()
            )

            _write_skill(
                settings.skills_dir / "new-skill" / "SKILL.md",
                "new-skill",
                "新安装的 Skill",
                "下一回合自动可见。",
            )
            refreshed = session.skills_service.snapshot()

        self.assertEqual(
            tuple(skill.name for skill in refreshed),
            ("skill-installer", "new-skill"),
        )

    def test_disabled_name_is_filtered_at_session_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "vault"
            workspace.mkdir()
            session_db_path = root / "plugin-data" / "sessions.db"
            skills_root = session_db_path.parent / "skills"
            _write_skill(
                skills_root / "disabled" / "SKILL.md",
                "disabled",
                "关闭时不可见",
                "关闭时不应加载。",
            )
            _write_skill(
                skills_root / "enabled" / "SKILL.md",
                "enabled",
                "保持可用",
                "正常加载。",
            )
            settings = Settings(
                api_key=None,
                model="test-model",
                base_url="http://unused",
                system_prompt="test system",
                workspace=workspace,
                shell_enabled=False,
                request_timeout_seconds=1,
                session_db_path=session_db_path,
                disabled_skills=frozenset({"disabled"}),
            )
            session = create_session(settings, AgentHandle(), RecordingClient())

            skills = session.skills_service.snapshot()
            rendered = render_available_skills(skills)

        self.assertEqual(
            tuple(skill.name for skill in skills), ("skill-installer", "enabled")
        )
        self.assertNotIn("关闭时不可见", rendered or "")
        self.assertIn("$enabled: 保持可用", rendered or "")

    def test_catalog_shortens_descriptions_before_omitting_skills(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skills = tuple(
                Skill(
                    f"large-{index}",
                    "很长的描述" * 2_000,
                    root / f"large-{index}" / "SKILL.md",
                )
                for index in range(2)
            )
            with self.assertLogs("agent.skills.render", level="WARNING"):
                rendered = render_available_skills(skills)

        assert rendered is not None
        self.assertLessEqual(len(rendered.encode("utf-8")), 8_000)
        self.assertIn("$large-0", rendered)
        self.assertIn("$large-1", rendered)
        self.assertIn("Skill 描述已缩短", rendered)
        self.assertNotIn("个 Skill 未显示", rendered)

    def test_catalog_omission_does_not_break_explicit_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skills = tuple(
                Skill(
                    f"skill-{index:03}",
                    "用于确认最小目录不会保留描述",
                    root / f"skill-{index:03}" / "SKILL.md",
                )
                for index in range(200)
            )
            explicit = skills[-1]
            _write_skill(
                explicit.path,
                explicit.name,
                explicit.description,
                "即使目录省略，也应注入这段正文。",
            )
            with self.assertLogs("agent.skills.render", level="WARNING"):
                rendered = render_available_skills(skills)
            injections = build_skill_injections(
                collect_explicit_mentions(f"请使用 ${explicit.name}", skills)
            )

        assert rendered is not None
        self.assertLessEqual(len(rendered.encode("utf-8")), 8_000)
        self.assertIn("$skill-000", rendered)
        self.assertNotIn("$skill-199", rendered)
        self.assertIn("所有描述已移除", rendered)
        self.assertIn("即使目录省略，也应注入这段正文", injections[0])

    def test_relative_script_is_detected_but_read_only_reference_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            skill_dir = workspace / "skills" / "scripted"
            script = skill_dir / "scripts" / "check.py"
            script.parent.mkdir(parents=True)
            script.write_text("print('ok')\n", encoding="utf-8")
            skill = Skill("scripted", "运行自带脚本", skill_dir / "SKILL.md")
            relative_script = script.relative_to(workspace).as_posix()

            invoked = detect_implicit_skill_invocations(
                f'python "{relative_script}"', (skill,), workspace
            )
            read_only = detect_implicit_skill_invocations(
                f'python --version & Get-Content "{relative_script}"', (skill,), workspace
            )

        self.assertEqual(invoked, (skill,))
        self.assertEqual(read_only, ())


def _write_skill(path: Path, name: str, description: str, body: str) -> None:
    """测试夹具写入真实 SKILL.md，覆盖 loader 的文件扫描边界。"""

    path.parent.mkdir(parents=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )
