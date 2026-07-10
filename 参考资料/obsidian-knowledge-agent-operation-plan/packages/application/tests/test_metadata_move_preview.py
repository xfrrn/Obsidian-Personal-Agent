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

from support import build_system


class MetadataMovePreviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_update_and_move_updates_simple_wikilink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = build_system(Path(directory))
            source = VaultPath("Inbox/idea.md")
            await system.vault.write_new(source, "---\ntags:\n  - inbox\n---\n\n# Idea")
            await system.vault.write_new(VaultPath("index.md"), "See [[idea]]")
            state = await system.vault.state(source)
            metadata = KnowledgeOperation(
                operation_id=OperationId("op_metadata"),
                operation_type=OperationType.UPDATE_METADATA,
                order=0,
                description="Set project metadata",
                reason="Classify note",
                risk_level=RiskLevel.MEDIUM,
                input=UpdateMetadataInput(
                    path=source,
                    expected_metadata_hash=state.metadata_hash,
                    set_values={"project": "AutoUp"},
                    add_tags=(TagName("AutoUp"),),
                ),
                idempotency_key=IdempotencyKey("operation:metadata:idea"),
            )
            move = KnowledgeOperation(
                operation_id=OperationId("op_move"),
                operation_type=OperationType.MOVE_NOTE,
                order=1,
                description="Move note",
                reason="Archive into project",
                risk_level=RiskLevel.MEDIUM,
                input=MoveNoteInput(
                    source_path=source,
                    target_path=VaultPath("Projects/AutoUp/idea.md"),
                    expected_version_hash=state.version_hash,
                    update_links=True,
                ),
                depends_on=(metadata.operation_id,),
                idempotency_key=IdempotencyKey("operation:move:idea"),
            )
            # Metadata changes the version, so a safe planner must re-read before generating move.
            # This test intentionally keeps the operations separate and checks preview only.
            plan = system.builder.build(
                instruction="Classify idea",
                summary="Update metadata and propose move",
                source=OperationSource(
                    actor_type=ActorType.USER,
                    actor_id=Identifier("user_local"),
                    remote=False,
                ),
                operations=[metadata],
                idempotency_key=IdempotencyKey("plan:metadata:idea"),
            )
            await system.plans.save(plan)
            preview = await system.preview.build(plan, system.context)
            self.assertEqual(preview.affected_file_count, 1)
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
            text = await system.vault.read(source)
            self.assertIn("project: AutoUp", text)
            self.assertIn("autoup", text)
