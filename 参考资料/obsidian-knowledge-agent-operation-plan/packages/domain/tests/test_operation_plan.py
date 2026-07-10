import unittest

from oka_domain.common import (
    Identifier,
    IdempotencyKey,
    OperationId,
    ResourceType,
    Sha256Hash,
    VaultPath,
)
from oka_domain.operations import (
    ActorType,
    AffectedResource,
    ApproverType,
    ConflictPolicy,
    CreateNoteInput,
    FailurePolicy,
    KnowledgeOperation,
    OperationPlan,
    OperationSource,
    OperationType,
    ResourceAction,
    RiskLevel,
    RollbackMode,
    RollbackPolicy,
)
from oka_domain.exceptions import OperationPlanError

HASH = Sha256Hash("a" * 64)


class OperationPlanTests(unittest.TestCase):
    def build_operation(self, operation_id: str, order: int, depends_on=()):
        return KnowledgeOperation(
            operation_id=OperationId(operation_id),
            operation_type=OperationType.CREATE_NOTE,
            order=order,
            description="Create a note",
            reason="User requested it",
            risk_level=RiskLevel.MEDIUM,
            input=CreateNoteInput(
                path=VaultPath(f"Inbox/{operation_id}.md"),
                content="hello",
                conflict_policy=ConflictPolicy.FAIL,
            ),
            depends_on=depends_on,
            idempotency_key=IdempotencyKey(f"create:{operation_id}"),
        )

    def test_plan_requires_confirmation_and_executes_after_approval(self) -> None:
        operation = self.build_operation("op_first", 0)
        plan = OperationPlan.create(
            instruction="Create note",
            summary="Create one Inbox note",
            source=OperationSource(
                actor_type=ActorType.USER,
                actor_id=Identifier("user_local"),
                remote=False,
            ),
            operations=[operation],
            affected_resources=[
                AffectedResource(
                    resource_type=ResourceType.NOTE,
                    action=ResourceAction.CREATE,
                    path=VaultPath("Inbox/op_first.md"),
                )
            ],
            rollback_policy=RollbackPolicy(
                mode=RollbackMode.REQUIRED,
                snapshot_required=True,
                retention_seconds=86400,
            ),
            failure_policy=FailurePolicy.ROLLBACK_ALL,
            idempotency_key=IdempotencyKey("plan:create:first"),
        )
        self.assertTrue(plan.requires_confirmation)
        plan.confirm(
            approver_type=ApproverType.LOCAL_USER,
            approver_id=Identifier("user_local"),
        )
        plan.start_execution()
        plan.mark_completed()
        self.assertEqual(plan.status.value, "completed")

    def test_dependency_must_point_to_earlier_operation(self) -> None:
        op1 = self.build_operation("op_first", 1)
        op2 = self.build_operation("op_second", 0, depends_on=(op1.operation_id,))
        with self.assertRaises(OperationPlanError):
            OperationPlan.create(
                instruction="Invalid order",
                summary="Dependency order is wrong",
                source=OperationSource(
                    actor_type=ActorType.USER,
                    actor_id=Identifier("user_local"),
                    remote=False,
                ),
                operations=[op1, op2],
                affected_resources=[
                    AffectedResource(
                        resource_type=ResourceType.NOTE,
                        action=ResourceAction.CREATE,
                        path=VaultPath("Inbox/op_first.md"),
                    )
                ],
                rollback_policy=RollbackPolicy(
                    mode=RollbackMode.BEST_EFFORT,
                    snapshot_required=True,
                    retention_seconds=86400,
                ),
                failure_policy=FailurePolicy.STOP,
                idempotency_key=IdempotencyKey("plan:invalid:order"),
            )


if __name__ == "__main__":
    unittest.main()
