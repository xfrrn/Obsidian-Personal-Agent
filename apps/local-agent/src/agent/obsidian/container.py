"""将 CodeX-Agent 运行时绑定到 Obsidian Vault 业务能力。"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .settings import LocalAgentSettings
from .operation_plans import (
    ExecutionMode,
    ExecutionPolicy,
    FilesystemOperationPlanExecutor,
    OperationManager,
    PersistentOperationPlanStore,
    SimpleOperationPlanner,
)
from .tool_provider import (
    FunctionTool,
    ObsidianToolDependencies,
    VaultAccessLedger,
    build_obsidian_tools,
)
from .vault import LocalDirectoryVaultRepository
from agent.config.loader import load_project_instructions, load_system_instructions
from agent.config.settings import Settings
from agent.web.server import AgentRuntime


@dataclass(frozen=True)
class LocalAgentContainer:
    runtime: AgentRuntime
    tools: tuple[FunctionTool, ...]
    operations: OperationManager
    access_ledger: VaultAccessLedger


def build_container(settings: LocalAgentSettings) -> LocalAgentContainer:
    """创建每个 Vault 唯一的运行时、工具和安全写入边界。"""

    if settings.vault_root is None:
        raise RuntimeError("vault root is not configured")
    if not settings.llm_base_url or not settings.llm_model:
        raise RuntimeError("model endpoint and model are required")

    vault = LocalDirectoryVaultRepository(settings.vault_root)
    data_dir = settings.vault_root / ".obsidian-agent-data"
    policy = ExecutionPolicy(ExecutionMode(settings.execution_mode))
    plan_store = PersistentOperationPlanStore(data_dir / "state.sqlite3")
    planner = SimpleOperationPlanner(settings.vault_root, policy)
    executor = FilesystemOperationPlanExecutor(settings.vault_root, plan_store, data_dir / "audit.jsonl")
    operations = OperationManager(planner, plan_store, executor, policy)
    access_ledger = VaultAccessLedger()
    tools = build_obsidian_tools(
        ObsidianToolDependencies(
            notes=vault,
            tasks=vault,
            operation_planner=planner,
            operation_plan_store=plan_store,
            operation_executor=executor,
            access_ledger=access_ledger,
        )
    )
    agent_settings = Settings(
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        base_url=_validated_base_url(settings.llm_base_url),
        system_prompt=load_system_instructions(
            "friendly",
            project_instructions=load_project_instructions(settings.vault_root),
        ),
        workspace=settings.vault_root,
        shell_enabled=False,
        request_timeout_seconds=60,
        max_tool_rounds=8,
        session_db_path=data_dir / "sessions.sqlite3",
    )
    return LocalAgentContainer(
        runtime=AgentRuntime(
            agent_settings,
            extra_handlers=tools,
            enable_apply_patch=False,
            include_world_state=False,
        ),
        tools=tools,
        operations=operations,
        access_ledger=access_ledger,
    )


def _validated_base_url(value: str) -> str:
    url = urlparse(value)
    local = url.hostname in {"localhost", "127.0.0.1", "::1"}
    if not url.hostname or url.scheme not in {"http", "https"}:
        raise ValueError("model endpoint must be an HTTP(S) URL")
    if url.scheme != "https" and not local:
        raise ValueError("model endpoint must use HTTPS unless it is localhost")
    if url.username or url.password:
        raise ValueError("model endpoint cannot contain credentials")
    return value.rstrip("/")
