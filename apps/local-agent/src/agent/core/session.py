"""一次 Agent 运行所共享的状态与服务容器。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from agent.config.settings import Settings
from agent.core.context_injection.base import ContextContributor
from agent.core.context_window import ContextWindow
from agent.core.token_counter import TokenCounter
from agent.core.scheduling.input_queue import InputQueue
from agent.core.turn.bus import TurnEventBus
from agent.core.turn.conversation import ConversationHistory
from agent.core.turn.events import TurnEvent
from agent.core.turn.running import RunningTask
from agent.protocol.mode import ModeKind
from agent.skills.service import SkillsService
from agent.storage import SessionStore
from agent.tools.registry import ToolRegistry
from agent.tools.router import ToolRouter
from agent.tools.runtime import ToolCallRuntime
from agent.tools.processes import ProcessManager
from agent.tools.types import PlanUpdate


@dataclass(slots=True)
class Session:
    """核心循环需要的最小运行时状态。

    ``client`` 使用结构化鸭子类型而非额外的抽象层；生产客户端与测试假客户端
    只要都提供同一个 ``complete`` 协程即可，MVP 不需要为此增加工厂或接口文件。
    """

    config: Settings
    client: Any
    tools: ToolRegistry
    tool_router: ToolRouter
    tool_runtime: ToolCallRuntime
    skills_service: SkillsService
    context_contributors: tuple[ContextContributor, ...]
    input_queue: InputQueue
    process_manager: ProcessManager | None = None
    session_id: str | None = None
    store: SessionStore | None = None
    turn_events: TurnEventBus = field(default_factory=TurnEventBus)
    conversation: ConversationHistory = field(default_factory=ConversationHistory)
    context_window: ContextWindow = field(default_factory=ContextWindow)
    # 保留直接构造 Session 的兼容入口；组合根会注入供上下文工具共享的实例。
    token_counter: TokenCounter = field(default=None)  # type: ignore[assignment]
    active_task: RunningTask | None = None
    mode: ModeKind = ModeKind.DEFAULT
    current_plan: PlanUpdate | None = None

    def __post_init__(self) -> None:
        if self.token_counter is None:
            self.token_counter = TokenCounter(self.config.model)

    @property
    def history(self) -> list[dict[str, Any]]:
        """兼容既有测试与压缩代码；新回合代码应通过 conversation 访问历史。"""

        return self.conversation.messages

    def emit(self, event: TurnEvent) -> None:
        """向同步 listener 和旁路 watcher 分发内部回合事实。"""

        self.turn_events.emit(event)

    def close(self) -> None:
        if self.process_manager is not None:
            self.process_manager.close()
        self.turn_events.close()

    async def aclose(self) -> None:
        if self.process_manager is not None:
            await self.process_manager.aclose()
        self.turn_events.close()

    async def persist_messages(
        self,
        submission_id: int | None,
        messages: tuple[tuple[dict[str, Any], bool], ...],
        turn_state: str,
        plan_update: PlanUpdate | None = None,
    ) -> None:
        if self.store is not None and self.session_id is not None:
            await asyncio.to_thread(
                self.store.append_messages,
                self.session_id,
                submission_id,
                messages,
                turn_state,
                plan_update,
            )
        if plan_update is not None:
            self.current_plan = plan_update

    async def persist_compaction(self) -> None:
        if self.store is not None and self.session_id is not None:
            summary = self.context_window.summary
            if summary is not None:
                await asyncio.to_thread(
                    self.store.save_compaction,
                    self.session_id,
                    self.context_window.number,
                    summary,
                )

    async def mark_turn_state(self, state: str) -> None:
        if self.store is not None and self.session_id is not None:
            await asyncio.to_thread(self.store.set_turn_state, self.session_id, state)

    async def persist_mode(self) -> None:
        if self.store is not None and self.session_id is not None:
            await asyncio.to_thread(self.store.set_mode, self.session_id, self.mode)
