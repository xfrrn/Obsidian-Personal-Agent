"""Application container and port-adapter binding."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

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
from intent import IntentClassifierOptions, RuleBasedIntentClassifier  # noqa: E402
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
    intent_classifier = RuleBasedIntentClassifier(
        options=IntentClassifierOptions(confirmation_threshold=0.5)
    )
    return LocalAgentContainer(
        runtime=AgentRuntime(registry, intent_classifier=intent_classifier),
        registry=registry,
        operations=operations,
    )
