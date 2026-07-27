"""Agent 运行时接收的操作（operation）定义。

本模块只包含 Python 标准库类型，不能 import 项目中的任何模块。这样协议层
可以被 CLI、测试或未来的 Web 入口共同使用，而不会把运行时依赖反向带进来。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from agent.protocol.mode import ModeKind


@dataclass(frozen=True, slots=True)
class UserInput:
    """一条来自用户的新输入。"""

    text: str
    # None 沿用会话当前选择，让既有嵌入式调用保持兼容。
    mode: ModeKind | None = None


@dataclass(frozen=True, slots=True)
class Interrupt:
    """中断当前或指定回合，保留已完成操作的运行结果。"""

    # 控制操作可能在 UI 延迟后才送达；带上目标可避免它误中断后来启动的新回合。
    target_submission_id: int | None = None


@dataclass(frozen=True, slots=True)
class CancelTool:
    """取消指定回合中的一个工具调用，而不取消整个回合。"""

    call_id: str
    # ``None`` 表示当前活跃回合，供只展示单个会话的入口保持最短调用方式。
    target_submission_id: int | None = None


@dataclass(frozen=True, slots=True)
class ResolveApproval:
    """批准或拒绝当前回合中一个仍在等待的工具调用。"""

    call_id: str
    approved: bool
    target_submission_id: int


@dataclass(frozen=True, slots=True)
class Shutdown:
    """请求停止事件循环，并取消尚未完成的回合。"""


Op: TypeAlias = UserInput | Interrupt | CancelTool | ResolveApproval | Shutdown
