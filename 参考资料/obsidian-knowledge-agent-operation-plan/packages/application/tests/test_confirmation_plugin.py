import tempfile
import unittest
from pathlib import Path

from oka_application.operations.errors import OperationConfirmationError
from oka_domain.common import CapabilityId, Identifier, IdempotencyKey, OperationId
from oka_domain.operations import (
    ActorType,
    ApproverType,
    InvokePluginInput,
    KnowledgeOperation,
    OperationSource,
    OperationType,
    RiskLevel,
)
from oka_operation_adapters.plugins import RegisteredPluginCapability

from support import build_system


class ConfirmationPluginTests(unittest.IsolatedAsyncioTestCase):
    async def test_plugin_invocation_and_one_time_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            system = build_system(Path(directory))
            calls = []

            async def invoke(parameters):
                calls.append(dict(parameters))
                return {"ok": True}

            system.plugins.register(
                RegisteredPluginCapability(
                    plugin_id="dataview",
                    capability=CapabilityId("dataview.refresh"),
                    invoke=invoke,
                )
            )
            operation = KnowledgeOperation(
                operation_id=OperationId("op_plugin_refresh"),
                operation_type=OperationType.INVOKE_PLUGIN,
                order=0,
                description="Refresh Dataview",
                reason="Update derived views",
                risk_level=RiskLevel.HIGH,
                input=InvokePluginInput(
                    plugin_id="dataview",
                    capability=CapabilityId("dataview.refresh"),
                    parameters={"scope": "vault"},
                ),
                idempotency_key=IdempotencyKey("operation:plugin:refresh"),
            )
            plan = system.builder.build(
                instruction="Refresh Dataview",
                summary="Invoke registered plugin capability",
                source=OperationSource(
                    actor_type=ActorType.USER,
                    actor_id=Identifier("user_local"),
                    remote=False,
                ),
                operations=[operation],
                idempotency_key=IdempotencyKey("plan:plugin:refresh"),
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
            self.assertEqual(len(calls), 1)
            # The same plan returns the stored execution before token verification.
            repeated = await system.coordinator.execute(
                plan_id=plan.plan_id,
                plan_version=plan.plan_version,
                context=system.context,
                confirmation_token=token,
            )
            self.assertEqual(repeated.execution_id, execution.execution_id)
            self.assertEqual(len(calls), 1)

            # Directly verifying the already consumed token is rejected.
            with self.assertRaises(OperationConfirmationError):
                system.tokens.verify(token)
