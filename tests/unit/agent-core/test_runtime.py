from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "agent-core"))

from runtime import AgentRuntime, CancellationToken, RuntimeRequest  # noqa: E402
from tools import (  # noqa: E402
    ToolDefinition,
    ToolEffect,
    ToolInvocationPolicy,
    ToolPermission,
    ToolPolicy,
    ToolRegistry,
    ToolRiskLevel,
)


def _registry() -> ToolRegistry:
    registry = ToolRegistry(ToolPolicy.allow({ToolPermission.READ, ToolPermission.WRITE}, ToolRiskLevel.HIGH))
    registry.register_function(
        ToolDefinition("list_tasks", "list tasks"),
        lambda _input, _context: {"message": "1 task"},
    )
    registry.register_function(
        ToolDefinition(
            "execute_operation_plan",
            "execute operation plan",
            permission=ToolPermission.WRITE,
            risk_level=ToolRiskLevel.HIGH,
            effect=ToolEffect.WRITE,
            invocation_policy=ToolInvocationPolicy.SYSTEM_ONLY,
            requires_confirmation=True,
        ),
        lambda input_data, _context: {"message": f"executed {input_data['operationPlanId']}"},
    )
    return registry


def test_runtime_requires_agent_client() -> None:
    try:
        asyncio.run(AgentRuntime(_registry()).run(RuntimeRequest("query tasks")))
    except RuntimeError as exc:
        assert "agent LLM is not configured" in str(exc)
    else:
        raise AssertionError("runtime should require an agent LLM client")


def test_runtime_honors_cancellation() -> None:
    token = CancellationToken()
    token.cancel()

    def agent(_messages, _tools):
        return {"final_answer": "unused"}

    try:
        asyncio.run(AgentRuntime(_registry(), agent_client=agent).run(RuntimeRequest("query tasks"), cancellation=token))
    except RuntimeError:
        pass
    else:
        raise AssertionError("cancelled run should fail")


def test_agent_loop_can_call_tools_until_final_answer() -> None:
    calls: list[str] = []
    streamed: list[str] = []
    registry = ToolRegistry(ToolPolicy.allow({ToolPermission.READ}, ToolRiskLevel.LOW))
    registry.register_function(
        ToolDefinition("search_notes", "search notes"),
        lambda _input, _context: {"results": [{"path": "A.md"}]},
    )
    registry.register_function(
        ToolDefinition("read_note", "read note"),
        lambda input_data, _context: {"message": f"read {input_data['path']}"},
    )

    def agent(messages, _tools):
        tool_messages = [message for message in messages if message["role"] == "tool"]
        if not tool_messages:
            calls.append("search_notes")
            return {"tool_calls": [{"name": "search_notes", "arguments": {"query": "agent"}}]}
        if len(tool_messages) == 1:
            calls.append("read_note")
            return {"tool_calls": [{"name": "read_note", "arguments": {"path": "A.md"}}]}
        return {"final_answer": "done"}

    result = asyncio.run(AgentRuntime(registry, agent_client=agent).run(
        RuntimeRequest("deep task"),
        on_trace=lambda step: streamed.append(step.tool_name),
    ))

    assert result.assistant_message == "done"
    assert calls == ["search_notes", "read_note"]
    assert streamed == ["search_notes", "read_note"]
    assert result.trace[0].detail["input"] == {"query": "agent"}
    assert result.trace[0].detail["results"] == [{"path": "A.md"}]
    assert result.trace[1].detail["input"] == {"path": "A.md"}
    assert result.execution is not None
    assert [item.status for item in result.execution.step_results] == ["completed", "completed"]


def test_agent_loop_stops_at_max_rounds() -> None:
    registry = _registry()

    def agent(_messages, _tools):
        return {"tool_calls": [{"name": "list_tasks", "arguments": {}}]}

    result = asyncio.run(AgentRuntime(registry, agent_client=agent, max_agent_rounds=2).run(RuntimeRequest("loop")))

    assert "最大思考轮数" in result.assistant_message
    assert result.execution is not None
    assert len(result.execution.step_results) == 2
    assert result.execution.stopped


def test_agent_loop_does_not_bypass_system_only_write_tool() -> None:
    registry = _registry()

    def agent(messages, _tools):
        if any(message["role"] == "tool" for message in messages):
            return {"final_answer": "blocked"}
        return {"tool_calls": [{"name": "execute_operation_plan", "arguments": {"operationPlanId": "op_1"}}]}

    result = asyncio.run(AgentRuntime(registry, agent_client=agent).run(RuntimeRequest("execute directly")))

    assert result.assistant_message == "blocked"
    assert result.execution is not None
    assert result.execution.step_results[0].status == "failed"


def test_agent_loop_returns_bad_tool_json_to_model() -> None:
    registry = _registry()

    def agent(messages, _tools):
        if any(message.get("content", "").startswith("Tool call parse error:") for message in messages):
            return {"final_answer": "fixed"}
        return {"tool_calls": [{"function": {"name": "list_tasks", "arguments": "{"}}]}

    result = asyncio.run(AgentRuntime(registry, agent_client=agent).run(RuntimeRequest("bad json")))

    assert result.assistant_message == "fixed"


def test_write_followup_reuses_previous_intent_and_builds_operation_plan() -> None:
    registry = ToolRegistry(ToolPolicy.allow({ToolPermission.READ, ToolPermission.WRITE}, ToolRiskLevel.HIGH))
    for name in ("read_note", "search_notes"):
        registry.register_function(ToolDefinition(name, name), lambda _input, _context: {"message": "read"})
    registry.register_function(
        ToolDefinition(
            "build_operation_plan",
            "build operation plan",
            permission=ToolPermission.WRITE,
            effect=ToolEffect.PREPARE_WRITE,
        ),
        lambda input_data, _context: {"message": "已生成操作计划，等待确认。", "input": input_data},
    )
    exposed: list[set[str]] = []

    def agent(messages, tools):
        exposed.append({tool.name for tool in tools})
        current = next(message["content"] for message in reversed(messages) if message["role"] == "user")
        if current == "帮我执行":
            return {"tool_calls": [{
                "name": "build_operation_plan",
                "arguments": {"requestedOperations": [{
                    "type": "move-note",
                    "path": "00-Inbox/前端设计网站.md",
                    "targetPath": "06-Resources/前端设计/Kill AI Slop.md",
                }]},
            }]}
        return {"final_answer": "需要我帮你执行移动操作吗？（把文件从 00-Inbox/前端设计网站.md 移动到 06-Resources/前端设计/Kill AI Slop.md）"}

    runtime = AgentRuntime(registry, agent_client=agent)
    asyncio.run(runtime.run(RuntimeRequest("这个文件应该放到哪里", conversation_id="c1")))
    result = asyncio.run(runtime.run(RuntimeRequest("帮我执行", conversation_id="c1")))

    assert result.intent.intent.value == "note.archive"
    assert exposed[-1] == {"read_note", "search_notes", "build_operation_plan"}
    assert result.trace[0].tool_name == "build_operation_plan"
    assert result.trace[0].status == "completed"


def test_runtime_limits_note_inspection_tools_and_blocks_health_check() -> None:
    registry = ToolRegistry(ToolPolicy.allow({ToolPermission.READ}, ToolRiskLevel.LOW))
    for name in ("read_note", "search_notes", "inspect_note", "list_rules", "check_vault_health"):
        registry.register_function(ToolDefinition(name, name), lambda _input, _context: {"message": "called"})
    exposed: list[set[str]] = []

    def agent(messages, tools):
        exposed.append({tool.name for tool in tools})
        if not any(message["role"] == "tool" for message in messages):
            return {"tool_calls": [{"name": "check_vault_health", "arguments": {}}]}
        return {"final_answer": "done"}

    result = asyncio.run(AgentRuntime(registry, agent_client=agent).run(
        RuntimeRequest("@[[00-Inbox/前端设计网站.md]]你觉得这个文档应该放在哪里")
    ))

    assert result.intent.intent.value == "note.inspect"
    assert exposed[0] == {"read_note", "search_notes", "inspect_note", "list_rules"}
    assert result.trace[0].status == "failed"
    assert "not available" in result.trace[0].summary


if __name__ == "__main__":
    test_runtime_requires_agent_client()
    test_runtime_honors_cancellation()
