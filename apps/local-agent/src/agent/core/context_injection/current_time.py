"""向每个回合提供当前本地时间。"""

from __future__ import annotations

from datetime import datetime

from agent.core.turn.context import TurnContext


class CurrentTimeContributor:
    """在请求发出时读取时间，避免长生命周期 Session 使用过期时间。"""

    async def contribute(self, context: TurnContext) -> str:
        now = datetime.now().astimezone()
        timezone = now.tzname() or now.strftime("%z")
        return f"## 当前时间（自动检测）\n- {now:%Y-%m-%d %H:%M:%S} {timezone}"
