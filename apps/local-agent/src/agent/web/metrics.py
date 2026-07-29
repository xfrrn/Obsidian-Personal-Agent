"""Web 运行时的进程内监控汇总。"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from threading import Lock
from time import monotonic
from typing import Any

from agent.core.turn.events import (
    AssistantResponseReceived,
    ImplicitSkillInvocation,
    RuntimeShutdown,
    ToolRequested,
    ToolResult,
    ToolResultStatus,
    TurnError,
    TurnEvent,
    TurnFinished,
    TurnInterrupted,
    TurnStarted,
)


class AgentMetrics:
    """只从 TurnEvent 汇总可展示元数据，绝不保存对话或工具输出。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._started_at = monotonic()
        self._turn_started_at: dict[tuple[str | None, int], float] = {}
        self._recent: deque[dict[str, Any]] = deque(maxlen=20)
        self._skill_names: Counter[str] = Counter()
        self._explicit_skill_names: Counter[str] = Counter()
        self._implicit_skill_names: Counter[str] = Counter()
        self._turns = Counter[str]()
        self._tools = Counter[str]()
        self._tool_names: dict[str, Counter[str]] = defaultdict(Counter)
        self._llm_requests = 0
        self._streamed_responses = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0
        self._total_latency_ms = 0
        self._completed_latency_count = 0
        self._last_latency_ms: int | None = None

    def record(self, event: TurnEvent, session_id: str | None = None) -> None:
        """作为可靠 listener 同步运行，故只做常数时间的内存更新。"""

        with self._lock:
            match event:
                case TurnStarted(submission_id=submission_id, mentioned_skill_names=skills):
                    self._turns["started"] += 1
                    self._turn_started_at[(session_id, submission_id)] = monotonic()
                    for skill in skills:
                        self._skill_names[skill] += 1
                        self._explicit_skill_names[skill] += 1
                    self._add_recent("turn_started", f"回合 #{submission_id} 已开始")
                case AssistantResponseReceived(response=response, streamed=streamed):
                    self._llm_requests += 1
                    self._streamed_responses += int(streamed)
                    if response.usage is not None:
                        self._prompt_tokens += response.usage.prompt_tokens
                        self._completion_tokens += response.usage.completion_tokens
                        self._total_tokens += response.usage.total_tokens
                    self._add_recent("llm_response", "模型响应完成")
                case ToolRequested(invocation=invocation):
                    self._tools["requested"] += 1
                    self._tool_names[invocation.name]["calls"] += 1
                    self._add_recent("tool_requested", f"调用工具：{invocation.name}")
                case ToolResult(name=name, status=status):
                    self._tools[status.value] += 1
                    self._tool_names[name][status.value] += 1
                    label = {
                        ToolResultStatus.SUCCESS: "工具完成",
                        ToolResultStatus.ERROR: "工具失败",
                        ToolResultStatus.INTERRUPTED: "工具已中断",
                    }[status]
                    self._add_recent(status.value, f"{label}：{name}")
                case ImplicitSkillInvocation(skill_name=skill_name):
                    self._skill_names[skill_name] += 1
                    self._implicit_skill_names[skill_name] += 1
                    self._add_recent("skill_invocation", f"运行 Skill 脚本：{skill_name}")
                case TurnFinished(submission_id=submission_id):
                    self._turns["finished"] += 1
                    self._finish_turn(session_id, submission_id, "turn_finished", f"回合 #{submission_id} 已完成")
                case TurnInterrupted(submission_id=submission_id):
                    self._turns["interrupted"] += 1
                    self._finish_turn(session_id, submission_id, "turn_interrupted", f"回合 #{submission_id} 已中断")
                case TurnError(submission_id=submission_id):
                    self._turns["errored"] += 1
                    if submission_id is not None:
                        self._finish_turn(session_id, submission_id, "turn_error", f"回合 #{submission_id} 失败")
                    else:
                        self._add_recent("turn_error", "Agent 运行出错")
                case RuntimeShutdown():
                    self._add_recent("shutdown", "Agent 运行时已关闭")

    def snapshot(self) -> dict[str, Any]:
        """返回 JSON 友好的快照；成功率排除人为中断的工具调用。"""

        with self._lock:
            attempts = self._tools["success"] + self._tools["error"]
            success_rate = round(self._tools["success"] / attempts * 100, 1) if attempts else 0.0
            return {
                "uptime_seconds": int(monotonic() - self._started_at),
                "active_turns": len(self._turn_started_at),
                "turns": {
                    "started": self._turns["started"],
                    "finished": self._turns["finished"],
                    "errored": self._turns["errored"],
                    "interrupted": self._turns["interrupted"],
                    "average_latency_ms": round(self._total_latency_ms / self._completed_latency_count)
                    if self._completed_latency_count
                    else 0,
                    "last_latency_ms": self._last_latency_ms,
                },
                "tokens": {
                    "prompt": self._prompt_tokens,
                    "completion": self._completion_tokens,
                    "total": self._total_tokens,
                    "requests": self._llm_requests,
                    "streamed_responses": self._streamed_responses,
                },
                "tools": {
                    "calls": self._tools["requested"],
                    "success": self._tools["success"],
                    "error": self._tools["error"],
                    "interrupted": self._tools["interrupted"],
                    "success_rate": success_rate,
                    "by_name": {
                        name: _tool_snapshot(counts)
                        for name, counts in sorted(self._tool_names.items())
                    },
                },
                "skills": {
                    "invocations": sum(self._skill_names.values()),
                    "explicit_invocations": sum(self._explicit_skill_names.values()),
                    "implicit_invocations": sum(self._implicit_skill_names.values()),
                    "by_name": dict(self._skill_names.most_common()),
                },
                "recent": list(self._recent),
            }

    def _finish_turn(
        self, session_id: str | None, submission_id: int, kind: str, text: str
    ) -> None:
        started_at = self._turn_started_at.pop((session_id, submission_id), None)
        if started_at is not None:
            self._last_latency_ms = round((monotonic() - started_at) * 1000)
            self._total_latency_ms += self._last_latency_ms
            self._completed_latency_count += 1
        self._add_recent(kind, text)

    def _add_recent(self, kind: str, text: str) -> None:
        self._recent.appendleft({"kind": kind, "text": text, "at_ms": round(monotonic() * 1000)})


def _tool_snapshot(counts: Counter[str]) -> dict[str, int | float]:
    """中断不计入成功率分母，避免 steering 拉低工具自身成功率。"""

    attempts = counts["success"] + counts["error"]
    return {
        "calls": counts["calls"],
        "success": counts["success"],
        "error": counts["error"],
        "interrupted": counts["interrupted"],
        "success_rate": round(counts["success"] / attempts * 100, 1) if attempts else 0.0,
    }
