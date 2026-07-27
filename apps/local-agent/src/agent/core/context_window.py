"""压缩后上下文窗口的最小状态。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ContextWindow:
    """一个窗口由压缩摘要和其后的原始交互组成。"""

    number: int = 0
    summary: str | None = None
    new_window_requested: bool = False

    def start_new(self, summary: str) -> None:
        """用新摘要替代旧窗口，并保留单调编号供诊断使用。"""

        self.number += 1
        self.summary = summary

    def request_new(self) -> None:
        """请求在下一次模型调用前压缩，避免工具执行中途改写历史。"""

        self.new_window_requested = True

    def consume_new_request(self) -> bool:
        """消费一次性手动请求；失败时由模型决定是否再次请求。"""

        requested = self.new_window_requested
        self.new_window_requested = False
        return requested

    def render_summary(self) -> str | None:
        """以 assistant 历史消息注入摘要，避免把旧用户内容提升为系统指令。"""

        if self.summary is None:
            return None
        return f"以下是此前对话的压缩记录（上下文窗口 {self.number}）：\n{self.summary}"
