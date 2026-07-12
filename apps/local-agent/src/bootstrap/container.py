"""Application container and port-adapter binding."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any
from urllib.request import Request, urlopen

from .settings import LocalAgentSettings


ROOT = Path(__file__).resolve().parents[4]


def configure_import_paths() -> None:
    """Make repo-local Python packages importable without packaging yet."""
    for path in (
        ROOT,
        ROOT / "packages",
        ROOT / "packages" / "agent-core",
        ROOT / "apps" / "local-agent" / "src",
    ):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)


configure_import_paths()

from adapters.filesystem.local_directory.vault import LocalDirectoryVaultRepository  # noqa: E402
from application.tool_provider import ApplicationToolDependencies, build_application_tools  # noqa: E402
from infrastructure.operation_plans import (  # noqa: E402
    ExecutionMode,
    ExecutionPolicy,
    FilesystemOperationPlanExecutor,
    OperationManager,
    PersistentOperationPlanStore,
    SimpleOperationPlanner,
)
from runtime import AgentRuntime  # noqa: E402
from tools import ToolPermission, ToolPolicy, ToolRegistry, ToolRiskLevel  # noqa: E402
from tools import ToolDefinition  # noqa: E402


@dataclass(frozen=True)
class LocalAgentContainer:
    runtime: AgentRuntime
    registry: ToolRegistry
    operations: OperationManager


def build_container(settings: LocalAgentSettings) -> LocalAgentContainer:
    """Wire application tools to local adapters."""
    if settings.vault_root is None:
        raise RuntimeError("vault root is not configured")
    vault = LocalDirectoryVaultRepository(settings.vault_root)
    data_dir = settings.vault_root / ".obsidian-agent-data"
    policy = ExecutionPolicy(ExecutionMode(settings.execution_mode))
    plan_store = PersistentOperationPlanStore(data_dir / "state.sqlite3")
    planner = SimpleOperationPlanner(settings.vault_root, policy)
    executor = FilesystemOperationPlanExecutor(
        settings.vault_root,
        plan_store,
        data_dir / "audit.jsonl",
    )
    operations = OperationManager(planner, plan_store, executor, policy)
    registry = ToolRegistry(
        ToolPolicy.allow({ToolPermission.READ, ToolPermission.WRITE}, ToolRiskLevel.HIGH)
    )
    registry.extend(
        build_application_tools(
            ApplicationToolDependencies(
                notes=vault,
                tasks=vault,
                operation_planner=planner,
                operation_plan_store=plan_store,
                operation_executor=executor,
            )
        )
    )
    return LocalAgentContainer(
        runtime=AgentRuntime(
            registry,
            agent_client=_openai_compatible_agent_client(settings)
            if settings.intent_llm_base_url and settings.intent_llm_model
            else None,
        ),
        registry=registry,
        operations=operations,
    )


def _openai_compatible_agent_client(settings: LocalAgentSettings):
    def call(messages: list[dict[str, Any]], tools: tuple[ToolDefinition, ...]) -> dict[str, Any]:
        url = _chat_completions_url(str(settings.intent_llm_base_url))
        headers = {"Content-Type": "application/json"}
        if settings.intent_llm_api_key:
            headers["Authorization"] = f"Bearer {settings.intent_llm_api_key}"
        body = json.dumps({
            "model": settings.intent_llm_model,
            "messages": messages,
            "tools": [_agent_function_tool(tool) for tool in tools],
            "tool_choice": "auto",
        }).encode("utf-8")
        request = Request(url, data=body, headers=headers, method="POST")
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return _agent_chat_decision(payload)

    return call


def _chat_completions_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    return value if value.endswith("/chat/completions") else f"{value}/chat/completions"


def _agent_chat_decision(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("model response must be an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("model response missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ValueError("model choice must be an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ValueError("model response missing message")
    content = message.get("content")
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        return {
            "content": content if isinstance(content, str) else "",
            "tool_calls": [
                {
                    "id": str(item.get("id") or ""),
                    "function": item.get("function"),
                }
                for item in tool_calls
                if isinstance(item, dict)
            ],
        }
    if isinstance(content, str) and content.strip():
        return {"final_answer": content}
    raise ValueError("model response missing message content")


def _agent_function_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema or {"type": "object"},
        },
    }
