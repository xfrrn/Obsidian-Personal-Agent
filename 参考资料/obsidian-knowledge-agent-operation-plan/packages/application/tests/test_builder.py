import tempfile
import unittest
from pathlib import Path

from oka_domain.common import Identifier, IdempotencyKey, OperationId, VaultPath
from oka_domain.operations import (
    ActorType,
    ConflictPolicy,
    CreateNoteInput,
    KnowledgeOperation,
    OperationSource,
    OperationType,
    RiskLevel,
)

from support import build_system


class BuilderTests(unittest.TestCase):
    def test_remote_write_is_at_least_medium_risk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = build_system(Path(directory))
            operation = KnowledgeOperation(
                operation_id=OperationId("op_remote_create"),
                operation_type=OperationType.CREATE_NOTE,
                order=0,
                description="Create remote note",
                reason="Remote user requested it",
                risk_level=RiskLevel.LOW,
                input=CreateNoteInput(
                    path=VaultPath("Inbox/remote.md"),
                    content="hello",
                    conflict_policy=ConflictPolicy.FAIL,
                ),
                idempotency_key=IdempotencyKey("operation:remote:create"),
            )
            plan = system.builder.build(
                instruction="Create note remotely",
                summary="Create one Inbox note",
                source=OperationSource(
                    actor_type=ActorType.GATEWAY,
                    actor_id=Identifier("gateway_remote"),
                    remote=True,
                ),
                operations=[operation],
                idempotency_key=IdempotencyKey("plan:remote:create"),
            )
            self.assertEqual(plan.risk_level, RiskLevel.MEDIUM)
            self.assertTrue(plan.requires_confirmation)

    def test_protected_path_becomes_critical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = build_system(
                Path(directory), protected_paths=(VaultPath("Archive/Important"),)
            )
            operation = KnowledgeOperation(
                operation_id=OperationId("op_protected_create"),
                operation_type=OperationType.CREATE_NOTE,
                order=0,
                description="Create protected note",
                reason="Test protection",
                risk_level=RiskLevel.LOW,
                input=CreateNoteInput(
                    path=VaultPath("Archive/Important/new.md"),
                    content="secret",
                ),
                idempotency_key=IdempotencyKey("operation:protected:create"),
            )
            plan = system.builder.build(
                instruction="Create protected note",
                summary="A protected path will change",
                source=OperationSource(
                    actor_type=ActorType.USER,
                    actor_id=Identifier("user_local"),
                    remote=False,
                ),
                operations=[operation],
                idempotency_key=IdempotencyKey("plan:protected:create"),
            )
            self.assertEqual(plan.risk_level, RiskLevel.CRITICAL)
            self.assertTrue(plan.requires_confirmation)
