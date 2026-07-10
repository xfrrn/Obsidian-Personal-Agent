from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oka_application.operations import (
    ConfirmationService,
    DefaultPlanPreviewService,
    ExecutionContext,
    ExecutionCoordinator,
    ExecutorType,
    OperationHandlerRegistry,
    OperationPlanBuilder,
    OperationPlanValidator,
    RiskCalculator,
    RiskPolicyConfig,
    RollbackManager,
)
from oka_domain.common import Identifier, VaultPath
from oka_domain.operations import ActorType
from oka_operation_adapters import (
    HmacConfirmationTokenService,
    InMemoryAuditRepository,
    InMemoryExecutionLock,
    InMemoryExecutionRepository,
    InMemoryIdempotencyStore,
    InMemoryOperationPlanRepository,
    InMemoryPluginInvoker,
    InMemorySnapshotStore,
    LocalFilesystemVault,
    LocalRuntimeInspector,
    StaticPermissionAuthorizer,
    build_filesystem_handler_registry,
)


ALL_PERMISSIONS = frozenset(
    {
        "operation.execute",
        "operation.execute.high-risk",
        "operation.execute.critical",
        "note.create",
        "note.update",
        "note.move",
        "task.create",
        "plugin.invoke",
    }
)


@dataclass
class TestSystem:
    vault: LocalFilesystemVault
    plugins: InMemoryPluginInvoker
    registry: OperationHandlerRegistry
    plans: InMemoryOperationPlanRepository
    executions: InMemoryExecutionRepository
    idempotency: InMemoryIdempotencyStore
    snapshots: InMemorySnapshotStore
    audit: InMemoryAuditRepository
    tokens: HmacConfirmationTokenService
    validator: OperationPlanValidator
    builder: OperationPlanBuilder
    confirmation: ConfirmationService
    preview: DefaultPlanPreviewService
    rollback: RollbackManager
    coordinator: ExecutionCoordinator
    context: ExecutionContext


def build_system(root: Path, *, protected_paths: tuple[VaultPath, ...] = ()) -> TestSystem:
    vault = LocalFilesystemVault(root)
    plugins = InMemoryPluginInvoker()
    registry = build_filesystem_handler_registry(vault, plugins)
    plans = InMemoryOperationPlanRepository()
    executions = InMemoryExecutionRepository()
    idempotency = InMemoryIdempotencyStore()
    snapshots = InMemorySnapshotStore()
    audit = InMemoryAuditRepository()
    lock = InMemoryExecutionLock()
    tokens = HmacConfirmationTokenService(b"test-confirmation-secret-key-32-bytes-minimum")
    inspector = LocalRuntimeInspector(vault, plugins)
    validator = OperationPlanValidator(
        authorizer=StaticPermissionAuthorizer(),
        inspector=inspector,
        registry=registry,
    )
    builder = OperationPlanBuilder(
        RiskCalculator(RiskPolicyConfig(protected_paths=protected_paths))
    )
    confirmation = ConfirmationService(
        plans=plans,
        validator=validator,
        tokens=tokens,
    )
    preview = DefaultPlanPreviewService(registry)
    rollback = RollbackManager(
        plans=plans,
        executions=executions,
        snapshots=snapshots,
        audit=audit,
        registry=registry,
        idempotency=idempotency,
    )
    coordinator = ExecutionCoordinator(
        plans=plans,
        executions=executions,
        idempotency=idempotency,
        lock=lock,
        snapshots=snapshots,
        audit=audit,
        tokens=tokens,
        validator=validator,
        registry=registry,
        rollback_manager=rollback,
    )
    context = ExecutionContext(
        vault_id=Identifier("vault_test"),
        actor_id=Identifier("user_local"),
        actor_type=ActorType.USER,
        executor_type=ExecutorType.TEST,
        executor_instance_id=Identifier("executor_test"),
        permissions=ALL_PERMISSIONS,
        trace_id=Identifier("trace_test"),
    )
    return TestSystem(
        vault=vault,
        plugins=plugins,
        registry=registry,
        plans=plans,
        executions=executions,
        idempotency=idempotency,
        snapshots=snapshots,
        audit=audit,
        tokens=tokens,
        validator=validator,
        builder=builder,
        confirmation=confirmation,
        preview=preview,
        rollback=rollback,
        coordinator=coordinator,
        context=context,
    )
