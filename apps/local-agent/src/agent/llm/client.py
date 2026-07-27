"""创建模型会话的轻量入口。"""

from __future__ import annotations

from agent.config.settings import Settings
from agent.llm.session import ModelClientSession


class ModelClient:
    """保存连接配置并为每个 Agent Session 创建独立模型会话。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def create_session(self) -> ModelClientSession:
        """模型会话与 Agent Session 同生命周期，避免在核心层散落 HTTP 配置。"""

        return ModelClientSession(self._settings)
