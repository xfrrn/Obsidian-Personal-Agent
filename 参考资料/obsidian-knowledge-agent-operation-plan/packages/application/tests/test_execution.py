import tempfile
import unittest
from pathlib import Path

from oka_application.operations import ExecutionStatus, OperationResultStatus
from oka_domain.common import Identifier, IdempotencyKey, OperationId, VaultPath, sha256_text
from oka_domain.operations import (
    ActorType,
    ApproverType,
    ConflictPolicy,
    CreateNoteInput,
    CreateTaskInput,
    FailurePolicy,
    KnowledgeOperation,
    OperationSource,
    OperationType,
    RiskLevel,
    UpdateMode,
    UpdateNoteInput,
)

from support import build_system


def local_source():
    return OperationSource(
        actor_type=ActorType.USER,
        actor_id=Identifier("user_local"),
        remote=False,
    )


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_note_and_plan_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = build_system(Path(directory))
            operation = KnowledgeOperation(
                operation_id=OperationId("op_create_note"),
                operation_type=OperationType.CREATE_NOTE,
                order=0,
                description="Create Inbox note",
                reason="User requested it",
                risk_level=RiskLevel.LOW,
                input=CreateNoteInput(
                    path=VaultPath("Inbox/test.md"),
                    content="# Test\n\nHello",
                    frontmatter={"type": "note"},
                ),
                idempotency_key=IdempotencyKey("operation:create:note:test"),
            )
            plan = system.builder.build(
                instruction="Create a test note",
                summary="Create Inbox/test.md",
                source=local_source(),
                operations=[operation],
                idempotency_key=IdempotencyKey("plan:create:note:test"),
            )
            await system.plans.save(plan)
            first = await system.coordinator.execute(
                plan_id=plan.plan_id,
                plan_version=plan.plan_version,
                context=system.context,
            )
            second = await system.coordinator.execute(
                plan_id=plan.plan_id,
                plan_version=plan.plan_version,
                context=system.context,
            )
            self.assertEqual(first.execution_id, second.execution_id)
            self.assertEqual(first.status, ExecutionStatus.COMPLETED)
            text = await system.vault.read(VaultPath("Inbox/test.md"))
            self.assertIn("type: note", text)
            self.assertIn("Hello", text)

    async def test_version_conflict_stops_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = build_system(Path(directory))
            path = VaultPath("Projects/AutoUp/design.md")
            await system.vault.write_new(path, "# Design\n\nOld")
            state = await system.vault.state(path)
            operation = KnowledgeOperation(
                operation_id=OperationId("op_update_note"),
                operation_type=OperationType.UPDATE_NOTE,
                order=0,
                description="Replace note content",
                reason="Improve the design note",
                risk_level=RiskLevel.MEDIUM,
                input=UpdateNoteInput(
                    path=path,
                    expected_version_hash=state.version_hash,
                    update_mode=UpdateMode.REPLACE_CONTENT,
                    content="# Design\n\nNew",
                ),
                idempotency_key=IdempotencyKey("operation:update:note:test"),
            )
            plan = system.builder.build(
                instruction="Update design note",
                summary="Replace design note body",
                source=local_source(),
                operations=[operation],
                idempotency_key=IdempotencyKey("plan:update:note:test"),
            )
            await system.plans.save(plan)
            token = await system.confirmation.confirm(
                plan_id=plan.plan_id,
                plan_version=plan.plan_version,
                approver_type=ApproverType.LOCAL_USER,
                approver_id=Identifier("user_local"),
            )
            await system.vault.write_atomic(path, "# Design\n\nUser changed it")
            execution = await system.coordinator.execute(
                plan_id=plan.plan_id,
                plan_version=plan.plan_version,
                context=system.context,
                confirmation_token=token,
            )
            self.assertEqual(execution.status, ExecutionStatus.FAILED)
            self.assertEqual(execution.operation_results[0].error.code, "NOTE_VERSION_CONFLICT")
            self.assertIn("User changed it", await system.vault.read(path))

    async def test_rollback_all_compensates_completed_operations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = build_system(Path(directory))
            await system.vault.write_new(VaultPath("Inbox/existing.md"), "existing")
            first = KnowledgeOperation(
                operation_id=OperationId("op_create_first"),
                operation_type=OperationType.CREATE_NOTE,
                order=0,
                description="Create first note",
                reason="Part of atomic plan",
                risk_level=RiskLevel.LOW,
                input=CreateNoteInput(path=VaultPath("Inbox/first.md"), content="first"),
                idempotency_key=IdempotencyKey("operation:create:first:atomic"),
            )
            second = KnowledgeOperation(
                operation_id=OperationId("op_create_existing"),
                operation_type=OperationType.CREATE_NOTE,
                order=1,
                description="Create conflicting note",
                reason="Force compensation",
                risk_level=RiskLevel.LOW,
                input=CreateNoteInput(
                    path=VaultPath("Inbox/existing.md"),
                    content="new",
                    conflict_policy=ConflictPolicy.FAIL,
                ),
                depends_on=(first.operation_id,),
                idempotency_key=IdempotencyKey("operation:create:existing:atomic"),
            )
            plan = system.builder.build(
                instruction="Run atomic create plan",
                summary="Second operation will fail",
                source=local_source(),
                operations=[first, second],
                failure_policy=FailurePolicy.ROLLBACK_ALL,
                idempotency_key=IdempotencyKey("plan:create:atomic:test"),
            )
            await system.plans.save(plan)
            execution = await system.coordinator.execute(
                plan_id=plan.plan_id,
                plan_version=plan.plan_version,
                context=system.context,
            )
            self.assertEqual(execution.status, ExecutionStatus.ROLLED_BACK)
            self.assertFalse((await system.vault.state(VaultPath("Inbox/first.md"))).exists)
            self.assertEqual(
                execution.operation_results[0].status,
                OperationResultStatus.ROLLED_BACK,
            )

    async def test_create_task_appends_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = build_system(Path(directory))
            operation = KnowledgeOperation(
                operation_id=OperationId("op_create_task"),
                operation_type=OperationType.CREATE_TASK,
                order=0,
                description="Create task",
                reason="User requested a task",
                risk_level=RiskLevel.LOW,
                input=CreateTaskInput(
                    title="完成微信公众号适配",
                    target_path=VaultPath("Tasks/AutoUp.md"),
                    priority="high",
                    project="AutoUp",
                ),
                idempotency_key=IdempotencyKey("operation:create:task:wechat"),
            )
            plan = system.builder.build(
                instruction="Create an AutoUp task",
                summary="Append one task",
                source=local_source(),
                operations=[operation],
                idempotency_key=IdempotencyKey("plan:create:task:wechat"),
            )
            await system.plans.save(plan)
            execution = await system.coordinator.execute(
                plan_id=plan.plan_id,
                plan_version=plan.plan_version,
                context=system.context,
            )
            self.assertEqual(execution.status, ExecutionStatus.COMPLETED)
            text = await system.vault.read(VaultPath("Tasks/AutoUp.md"))
            self.assertIn("- [ ] 完成微信公众号适配", text)
            self.assertIn("优先级：high", text)
