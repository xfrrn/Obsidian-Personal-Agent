from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile

from oka_application.operations import (
    ExecutionContext,
    ExecutionCoordinator,
    ExecutorType,
    OperationPlanBuilder,
    OperationPlanValidator,
    RollbackManager,
)
from oka_domain.common import Identifier, IdempotencyKey, OperationId, VaultPath
from oka_domain.operations import (
    ActorType,
    CreateNoteInput,
    KnowledgeOperation,
    OperationSource,
    OperationType,
    RiskLevel,
)
from oka_operation_adapters import (
    HmacConfirmationTokenService,
    InMemoryAuditRepository,
    InMemoryExecutionLock,
    InMemoryExecutionRepository,
    InMemoryIdempotencyStore,
    InMemoryOperationPlanRepository,
    InMemorySnapshotStore,
    LocalFilesystemVault,
    LocalRuntimeInspector,
    StaticPermissionAuthorizer,
    build_filesystem_handler_registry,
)


async def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        vault = LocalFilesystemVault(Path(directory) / "vault")
        registry = build_filesystem_handler_registry(vault)
        plans = InMemoryOperationPlanRepository()
        executions = InMemoryExecutionRepository()
        idempotency = InMemoryIdempotencyStore()
        snapshots = InMemorySnapshotStore()
        audit = InMemoryAuditRepository()
        tokens = HmacConfirmationTokenService(b"example-confirmation-secret-32-bytes-minimum")
        validator = OperationPlanValidator(
            authorizer=StaticPermissionAuthorizer(),
            inspector=LocalRuntimeInspector(vault),
            registry=registry,
        )
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
            lock=InMemoryExecutionLock(),
            snapshots=snapshots,
            audit=audit,
            tokens=tokens,
            validator=validator,
            registry=registry,
            rollback_manager=rollback,
        )
        operation = KnowledgeOperation(
            operation_id=OperationId("op_example_create"),
            operation_type=OperationType.CREATE_NOTE,
            order=0,
            description="Create example note",
            reason="Demonstrate safe execution",
            risk_level=RiskLevel.LOW,
            input=CreateNoteInput(
                path=VaultPath("Inbox/example.md"),
                content="# Example\n\nCreated through Operation Plan.",
                frontmatter={"type": "note", "status": "inbox"},
            ),
            idempotency_key=IdempotencyKey("operation:example:create"),
        )
        plan = OperationPlanBuilder().build(
            instruction="Create an example note",
            summary="Create Inbox/example.md",
            source=OperationSource(
                actor_type=ActorType.USER,
                actor_id=Identifier("user_local"),
                remote=False,
            ),
            operations=[operation],
            idempotency_key=IdempotencyKey("plan:example:create"),
        )
        await plans.save(plan)
        context = ExecutionContext(
            vault_id=Identifier("vault_example"),
            actor_id=Identifier("user_local"),
            actor_type=ActorType.USER,
            executor_type=ExecutorType.TEST,
            executor_instance_id=Identifier("executor_example"),
            permissions=frozenset({"operation.execute", "note.create"}),
            trace_id=Identifier("trace_example"),
        )
        execution = await coordinator.execute(
            plan_id=plan.plan_id,
            plan_version=plan.plan_version,
            context=context,
        )
        print("Execution:", execution.status.value)
        print(await vault.read(VaultPath("Inbox/example.md")))


if __name__ == "__main__":
    asyncio.run(main())
