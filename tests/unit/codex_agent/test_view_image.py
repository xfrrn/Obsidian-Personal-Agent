"""view_image 的本地读取和模型输入链路。"""

from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agent.config.settings import Settings
from agent.core.agent_loop import run_turn
from agent.core.handle import AgentHandle
from agent.core.loop import create_session
from agent.core.turn.context import TurnContext
from agent.llm.types import AssistantResponse, ToolCall
from agent.tools.handlers.view_image import ViewImageTool


_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class _ImageClient:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AssistantResponse:
        self.calls += 1
        if self.calls == 1:
            assert any(
                tool["function"]["name"] == "view_image" for tool in tools
            )
            return AssistantResponse(
                None,
                (ToolCall("image-1", "view_image", {"path": "pixel.png"}),),
            )

        image_message = messages[-1]
        assert image_message["role"] == "user"
        image_part = image_message["content"][1]
        assert image_part["type"] == "image_url"
        assert image_part["image_url"]["url"].startswith(
            "data:image/png;base64,iVBOR"
        )
        return AssistantResponse("图片已读取")


class ViewImageToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_attaches_image_to_the_next_model_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "pixel.png").write_bytes(_ONE_PIXEL_PNG)
            client = _ImageClient()
            session = create_session(
                _settings(workspace), AgentHandle(), client=client
            )

            await run_turn(session, TurnContext(1, "查看图片", "test"))

        self.assertEqual(client.calls, 2)
        self.assertEqual(session.history[-1]["content"], "图片已读取")
        self.assertEqual(session.conversation.model_snapshot(), session.conversation.snapshot())

    async def test_tool_rejects_paths_outside_the_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "vault"
            workspace.mkdir()
            tool = ViewImageTool(_settings(workspace))

            with self.assertRaisesRegex(ValueError, "超出工作目录"):
                await tool.run({"path": "../outside.png"})


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


if __name__ == "__main__":
    unittest.main()
