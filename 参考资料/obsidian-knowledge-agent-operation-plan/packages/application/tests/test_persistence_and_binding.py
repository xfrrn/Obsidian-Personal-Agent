import tempfile
import unittest
from pathlib import Path

from oka_application.operations import ExecutionStatus
from oka_domain.common import Identifier, IdempotencyKey, OperationId, TagName, VaultPath
from oka_domain.operations import (
    ActorType,
    ApproverType,
    KnowledgeOperation,
    MoveNoteInput,
    OperationSource,
    OperationType,
    RiskLevel,
    UpdateMetadataInput,
)
from oka_operation_adapters import (
    FileExecutionLock,
    JsonOperationPlanRepository,
)

from support import build_system


class PersistenceAndBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_plan_round_trip_preserves_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = build_system(Path(directory) / "vault")
            path = VaultPath("Inbox/idea.md")
            await system.vault.write_new(path, "# Idea")
            state = await system.vault.state(path)
            operation = KnowledgeOperation(
                operation_id=OperationId("op_persist_metadata"),
                operation_type=OperationType.UPDATE_METADATA,
                order=0,
                description="Persist metadata operation",
                reason="Test repository",
                risk_level=RiskLevel.MEDIUM,
                input=UpdateMetadataInput(
                    path=path,
                    expected_metadata_hash=state.metadata_hash,
                    set_values={"project": "AutoUp"},
                ),
                idempotency_key=IdempotencyKey("operation:persist:metadata"),
            )
            plan = system.builder.build(
                instruction="Persist plan",
                summary="Round-trip JSON repository",
                source=OperationSource(
                    actor_type=ActorType.USER,
                    actor_id=Identifier("user_local"),
                    remote=False,
                ),
                operations=[operation],
                idempotency_key=IdempotencyKey("plan:persist:metadata"),
            )
            repository = JsonOperationPlanRepository(Path(directory) / "runtime")
            await repository.save(plan)
            loaded = await repository.get(plan.plan_id)
            self.assertIsNotNone(loaded)
            loaded.validate_integrity()
            self.assertEqual(loaded.plan_id, plan.plan_id)
            self.assertEqual(loaded.operations[0].operation_type, OperationType.UPDATE_METADATA)

    async def test_runtime_version_binding_allows_metadata_then_move(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = build_system(Path(directory))
            source = VaultPath("Inbox/idea.md")
            target = VaultPath("Projects/AutoUp/idea.md")
            await system.vault.write_new(source, "---\ntags: [inbox]\n---\n\n# Idea")
            await system.vault.write_new(VaultPath("index.md"), "See [[Inbox/idea]]")
            state = await system.vault.state(source)
            metadata = KnowledgeOperation(
                operation_id=OperationId("op_chain_metadata"),
                operation_type=OperationType.UPDATE_METADATA,
                order=0,
                description="Update metadata",
                reason="Classify note",
                risk_level=RiskLevel.MEDIUM,
                input=UpdateMetadataInput(
                    path=source,
                    expected_metadata_hash=state.metadata_hash,
                    set_values={"project": "AutoUp"},
                    add_tags=(TagName("AutoUp"),),
                ),
                idempotency_key=IdempotencyKey("operation:chain:metadata"),
            )
            move = KnowledgeOperation(
                operation_id=OperationId("op_chain_move"),
                operation_type=OperationType.MOVE_NOTE,
                order=1,
                description="Move classified note",
                reason="Place note in project",
                risk_level=RiskLevel.HIGH,
                input=MoveNoteInput(
                    source_path=source,
                    target_path=target,
                    expected_version_hash=state.version_hash,
                    update_links=True,
                ),
                depends_on=(metadata.operation_id,),
                idempotency_key=IdempotencyKey("operation:chain:move"),
                extensions={"expectedVersionFromOperation": str(metadata.operation_id)},
            )
            plan = system.builder.build(
                instruction="Classify and move note",
                summary="Update metadata before moving",
                source=OperationSource(
                    actor_type=ActorType.USER,
                    actor_id=Identifier("user_local"),
                    remote=False,
                ),
                operations=[metadata, move],
                idempotency_key=IdempotencyKey("plan:chain:metadata-move"),
            )
            await system.plans.save(plan)
            token = await system.confirmation.confirm(
                plan_id=plan.plan_id,
                plan_version=plan.plan_version,
                approver_type=ApproverType.LOCAL_USER,
                approver_id=Identifier("user_local"),
            )
            execution = await system.coordinator.execute(
                plan_id=plan.plan_id,
                plan_version=plan.plan_version,
                context=system.context,
                confirmation_token=token,
            )
            self.assertEqual(execution.status, ExecutionStatus.COMPLETED)
            self.assertFalse((await system.vault.state(source)).exists)
            self.assertTrue((await system.vault.state(target)).exists)
            self.assertIn("Projects/AutoUp/idea", await system.vault.read(VaultPath("index.md")))

    async def test_file_execution_lock_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            from oka_application.operations.models import ExecutionId
            from oka_domain.common import PlanId

            lock = FileExecutionLock(directory)
            plan_id = PlanId("plan_lock_test")
            first = await lock.acquire(
                plan_id=plan_id,
                execution_id=ExecutionId("exec_lock_first"),
                owner_instance_id=Identifier("executor_first"),
                ttl_seconds=60,
            )
            second = await lock.acquire(
                plan_id=plan_id,
                execution_id=ExecutionId("exec_lock_second"),
                owner_instance_id=Identifier("executor_second"),
                ttl_seconds=60,
            )
            self.assertIsNotNone(first)
            self.assertIsNone(second)
            await lock.release(first)
            third = await lock.acquire(
                plan_id=plan_id,
                execution_id=ExecutionId("exec_lock_third"),
                owner_instance_id=Identifier("executor_third"),
                ttl_seconds=60,
            )
            self.assertIsNotNone(third)
