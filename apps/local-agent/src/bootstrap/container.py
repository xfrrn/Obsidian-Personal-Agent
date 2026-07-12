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
from intent import (  # noqa: E402
    HybridIntentClassifier,
    IntentClassifierOptions,
    IntentEntityType,
    IntentType,
    RuleBasedIntentClassifier,
)
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
    intent_classifier = _build_intent_classifier(settings)
    return LocalAgentContainer(
        runtime=AgentRuntime(registry, intent_classifier=intent_classifier),
        registry=registry,
        operations=operations,
    )


def _build_intent_classifier(settings: LocalAgentSettings) -> object:
    rule_classifier = RuleBasedIntentClassifier(
        options=IntentClassifierOptions(confirmation_threshold=0.5)
    )
    if not (settings.intent_llm_base_url and settings.intent_llm_model):
        return rule_classifier
    return HybridIntentClassifier(
        _openai_compatible_intent_client(settings),
        rule_classifier=rule_classifier,
    )


def _openai_compatible_intent_client(settings: LocalAgentSettings):
    def call(prompt: str) -> str:
        url = _chat_completions_url(str(settings.intent_llm_base_url))
        headers = {"Content-Type": "application/json"}
        if settings.intent_llm_api_key:
            headers["Authorization"] = f"Bearer {settings.intent_llm_api_key}"
        body = json.dumps({
            "model": settings.intent_llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [_intent_function_tool()],
            "tool_choice": {"type": "function", "function": {"name": "classify_intent"}},
        }).encode("utf-8")
        request = Request(url, data=body, headers=headers, method="POST")
        with urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return _chat_content(payload)

    return call


def _chat_completions_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    return value if value.endswith("/chat/completions") else f"{value}/chat/completions"


def _chat_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ValueError("model response must be an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("model response missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ValueError("model choice must be an object")
    message = first.get("message")
    if isinstance(message, dict):
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            function = tool_calls[0].get("function") if isinstance(tool_calls[0], dict) else None
            arguments = function.get("arguments") if isinstance(function, dict) else None
            if isinstance(arguments, str):
                return arguments
        function_call = message.get("function_call")
        if isinstance(function_call, dict) and isinstance(function_call.get("arguments"), str):
            return function_call["arguments"]
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    raise ValueError("model response missing message content")


def _intent_function_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "classify_intent",
            "description": "Classify one Obsidian Personal Agent user intent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {"type": "string", "enum": [intent.value for intent in IntentType]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "requiresConfirmation": {"type": "boolean"},
                    "entities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": [item.value for item in IntentEntityType]},
                                "value": {"type": "string"},
                            },
                            "required": ["type", "value"],
                        },
                    },
                },
                "required": ["intent", "confidence", "requiresConfirmation", "entities"],
            },
        },
    }
