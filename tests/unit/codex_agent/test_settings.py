""".env 配置读取的最小验证。"""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agent.config.settings import Settings
from agent.permissions import ApprovalPolicy, SandboxMode
from agent.sandbox import SandboxBackend, SandboxNetwork
from agent.tools.handlers.exec_command import ExecCommandTool
from agent.tools.processes import ProcessManager


class SettingsEnvFileTest(unittest.TestCase):
    def test_dotenv_supplies_api_key_and_base_url_without_overriding_process_env(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "# 本地模型配置\n"
                "OPENAI_API_KEY=from-dotenv\n"
                "OPENAI_BASE_URL='https://model.example/v1'\n"
                "OPENAI_MODEL=dotenv-model # 支持行尾注释\n",
                encoding="utf-8",
            )
            with patch("agent.config.loader._PROJECT_ENV_FILE", env_file), patch.dict(
                os.environ, {"OPENAI_API_KEY": "from-process"}, clear=True
            ):
                settings = Settings.from_env()

        self.assertEqual(settings.api_key, "from-process")
        self.assertEqual(settings.base_url, "https://model.example/v1")
        self.assertEqual(settings.model, "dotenv-model")
        self.assertEqual(settings.temperature, 0)
        self.assertNotIn("## 运行环境（自动检测）", settings.system_prompt)
        self.assertIn("# 安全约束", settings.system_prompt)
        self.assertIn(
            "当前执行环境（自动检测）",
            ExecCommandTool(
                settings, ProcessManager(settings.request_timeout_seconds)
            ).spec.description,
        )
        self.assertEqual(settings.context_window_tokens, 128_000)
        self.assertEqual(settings.reserved_output_tokens, 8_192)
        self.assertEqual(settings.auto_compact_threshold, 115_200)
        self.assertIs(settings.sandbox_mode, SandboxMode.WORKSPACE_WRITE)
        self.assertIs(settings.approval_policy, ApprovalPolicy.ON_REQUEST)
        self.assertIs(settings.sandbox_backend, SandboxBackend.AUTO)
        self.assertIs(settings.sandbox_network, SandboxNetwork.HOST)

    def test_rejects_unknown_sandbox_mode(self) -> None:
        with patch("agent.config.settings.load_env_file"), patch.dict(
            os.environ, {"AGENT_SANDBOX_MODE": "best-effort"}, clear=True
        ):
            with self.assertRaisesRegex(ValueError, "AGENT_SANDBOX_MODE"):
                Settings.from_env()

    def test_rejects_unknown_sandbox_backend(self) -> None:
        with patch("agent.config.settings.load_env_file"), patch.dict(
            os.environ, {"AGENT_SANDBOX_BACKEND": "pretend"}, clear=True
        ):
            with self.assertRaisesRegex(ValueError, "AGENT_SANDBOX_BACKEND"):
                Settings.from_env()

    def test_native_and_legacy_windows_backend_names_use_built_in_backend(self) -> None:
        self.assertIs(
            SandboxBackend.parse("native-windows"), SandboxBackend.NATIVE_WINDOWS
        )
        self.assertIs(
            SandboxBackend.parse("codex-windows"), SandboxBackend.NATIVE_WINDOWS
        )

    def test_rejects_unknown_sandbox_network_policy(self) -> None:
        with patch("agent.config.settings.load_env_file"), patch.dict(
            os.environ, {"AGENT_SANDBOX_NETWORK": "sometimes"}, clear=True
        ):
            with self.assertRaisesRegex(ValueError, "AGENT_SANDBOX_NETWORK"):
                Settings.from_env()

    def test_rejects_sandbox_state_inside_writable_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("agent.config.settings.load_env_file"), patch.dict(
                os.environ,
                {
                    "AGENT_WORKSPACE": directory,
                    "AGENT_ENABLE_SHELL": "true",
                    "AGENT_SANDBOX_STATE": str(Path(directory) / ".sandbox"),
                },
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "AGENT_SANDBOX_STATE"):
                    Settings.from_env()

    def test_rejects_output_reserve_that_consumes_the_context_window(self) -> None:
        with patch("agent.config.settings.load_env_file"), patch.dict(
            os.environ,
            {"AGENT_CONTEXT_WINDOW_TOKENS": "100", "AGENT_RESERVED_OUTPUT_TOKENS": "100"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "AGENT_RESERVED_OUTPUT_TOKENS"):
                Settings.from_env()

    def test_clamps_auto_compact_limit_to_ninety_percent(self) -> None:
        with patch("agent.config.settings.load_env_file"), patch.dict(
            os.environ,
            {
                "AGENT_CONTEXT_WINDOW_TOKENS": "100",
                "AGENT_RESERVED_OUTPUT_TOKENS": "5",
                "AGENT_AUTO_COMPACT_TOKEN_LIMIT": "95",
            },
            clear=True,
        ):
            settings = Settings.from_env()

        self.assertEqual(settings.auto_compact_threshold, 90)

    def test_rejects_nonpositive_auto_compact_limit(self) -> None:
        with patch("agent.config.settings.load_env_file"), patch.dict(
            os.environ, {"AGENT_AUTO_COMPACT_TOKEN_LIMIT": "0"}, clear=True
        ):
            with self.assertRaisesRegex(ValueError, "AGENT_AUTO_COMPACT_TOKEN_LIMIT"):
                Settings.from_env()

    def test_auto_compact_configuration_can_only_lower_ninety_percent_limit(self) -> None:
        settings = Settings(
            api_key=None,
            model="test-model",
            base_url="http://unused",
            system_prompt="test",
            workspace=Path.cwd(),
            shell_enabled=False,
            request_timeout_seconds=1,
            max_tool_rounds=1,
            context_window_tokens=1_000,
            reserved_output_tokens=50,
            auto_compact_token_limit=940,
        )

        self.assertEqual(settings.auto_compact_threshold, 900)
        self.assertEqual(
            replace(settings, auto_compact_token_limit=700).auto_compact_threshold,
            700,
        )

    def test_rejects_temperature_outside_api_range(self) -> None:
        with patch("agent.config.settings.load_env_file"), patch.dict(
            os.environ, {"OPENAI_TEMPERATURE": "2.1"}, clear=True
        ):
            with self.assertRaisesRegex(ValueError, "OPENAI_TEMPERATURE"):
                Settings.from_env()

    def test_workspace_agents_instructions_are_added_before_safety(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "AGENTS.md").write_text("只运行项目测试", encoding="utf-8")
            with patch("agent.config.settings.load_env_file"), patch.dict(
                os.environ, {"AGENT_WORKSPACE": str(workspace)}, clear=True
            ):
                settings = Settings.from_env()

        self.assertIn("只运行项目测试", settings.system_prompt)
        self.assertLess(settings.system_prompt.index("只运行项目测试"), settings.system_prompt.index("# 安全约束"))
