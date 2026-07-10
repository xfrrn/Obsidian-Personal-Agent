from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from oka_application.operations.models import (
    ExecutionId,
    ExecutionStatus,
    ExecutorType,
    OperationError,
    OperationExecution,
    OperationResult,
    OperationResultStatus,
)
from oka_domain.common import (
    CapabilityId,
    Identifier,
    IdempotencyKey,
    OperationId,
    PlanId,
    ResourceType,
    Sha256Hash,
    TagName,
    VaultPath,
)
from oka_domain.operations import (
    ActorType,
    AffectedResource,
    ApproverType,
    ConfirmationMode,
    ConfirmationPolicy,
    ConflictPolicy,
    CreateNoteInput,
    CreateTaskInput,
    ExpectedResult,
    FailurePolicy,
    InvokePluginInput,
    KnowledgeOperation,
    MoveNoteInput,
    OperationPlan,
    OperationPrecondition,
    OperationSource,
    OperationType,
    OperationWarning,
    PlanApproval,
    PlanStatus,
    PreconditionType,
    ResourceAction,
    RiskLevel,
    RollbackMode,
    RollbackPolicy,
    SectionReplacement,
    TextPatch,
    UpdateMetadataInput,
    UpdateMode,
    UpdateNoteInput,
    WarningSeverity,
)


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _hash(value: str | None) -> Sha256Hash | None:
    return Sha256Hash(value) if value else None


def operation_plan_to_dict(plan: OperationPlan) -> dict[str, Any]:
    return {
        "planId": str(plan.plan_id),
        "planVersion": plan.plan_version,
        "instruction": plan.instruction,
        "summary": plan.summary,
        "source": {
            "actorType": plan.source.actor_type.value,
            "actorId": str(plan.source.actor_id),
            "remote": plan.source.remote,
            "channel": plan.source.channel,
            "instructionId": str(plan.source.instruction_id) if plan.source.instruction_id else None,
        },
        "riskLevel": plan.risk_level.contract_value,
        "status": plan.status.value,
        "requiresConfirmation": plan.requires_confirmation,
        "operations": [_operation_to_dict(item) for item in plan.operations],
        "affectedResources": [_resource_to_dict(item) for item in plan.affected_resources],
        "warnings": [_warning_to_dict(item) for item in plan.warnings],
        "rollbackPolicy": {
            "mode": plan.rollback_policy.mode.value,
            "snapshotRequired": plan.rollback_policy.snapshot_required,
            "retentionSeconds": plan.rollback_policy.retention_seconds,
            "maxRollbackAgeSeconds": plan.rollback_policy.max_rollback_age_seconds,
        },
        "failurePolicy": plan.failure_policy.value,
        "idempotencyKey": str(plan.idempotency_key),
        "createdAt": plan.created_at.isoformat(),
        "expiresAt": plan.expires_at.isoformat() if plan.expires_at else None,
        "confirmationPolicy": _confirmation_to_dict(plan.confirmation_policy),
        "approval": _approval_to_dict(plan.approval),
        "integrity": str(plan.integrity) if plan.integrity else None,
        "extensions": dict(plan.extensions),
        "revision": plan.revision,
    }


def operation_plan_from_dict(data: Mapping[str, Any]) -> OperationPlan:
    source = data["source"]
    rollback = data["rollbackPolicy"]
    return OperationPlan(
        revision=int(data.get("revision", 0)),
        plan_id=PlanId(data["planId"]),
        plan_version=int(data["planVersion"]),
        instruction=data["instruction"],
        summary=data["summary"],
        source=OperationSource(
            actor_type=ActorType(source["actorType"]),
            actor_id=Identifier(source["actorId"]),
            remote=bool(source["remote"]),
            channel=source.get("channel"),
            instruction_id=Identifier(source["instructionId"]) if source.get("instructionId") else None,
        ),
        risk_level=RiskLevel[data["riskLevel"].upper()],
        status=PlanStatus(data["status"]),
        requires_confirmation=bool(data["requiresConfirmation"]),
        operations=tuple(_operation_from_dict(item) for item in data["operations"]),
        affected_resources=tuple(_resource_from_dict(item) for item in data.get("affectedResources", [])),
        warnings=tuple(_warning_from_dict(item) for item in data.get("warnings", [])),
        rollback_policy=RollbackPolicy(
            mode=RollbackMode(rollback["mode"]),
            snapshot_required=bool(rollback["snapshotRequired"]),
            retention_seconds=int(rollback["retentionSeconds"]),
            max_rollback_age_seconds=rollback.get("maxRollbackAgeSeconds"),
        ),
        failure_policy=FailurePolicy(data["failurePolicy"]),
        idempotency_key=IdempotencyKey(data["idempotencyKey"]),
        created_at=datetime.fromisoformat(data["createdAt"]),
        expires_at=_dt(data.get("expiresAt")),
        confirmation_policy=_confirmation_from_dict(data.get("confirmationPolicy")),
        approval=_approval_from_dict(data.get("approval")),
        integrity=_hash(data.get("integrity")),
        extensions=dict(data.get("extensions", {})),
    )


def execution_to_dict(execution: OperationExecution) -> dict[str, Any]:
    return {
        "executionId": str(execution.execution_id),
        "planId": str(execution.plan_id),
        "planVersion": execution.plan_version,
        "status": execution.status.value,
        "executorType": execution.executor_type.value,
        "executorInstanceId": str(execution.executor_instance_id),
        "startedAt": execution.started_at.isoformat(),
        "completedAt": execution.completed_at.isoformat() if execution.completed_at else None,
        "operationResults": [operation_result_to_dict(item) for item in execution.operation_results],
        "rollbackAvailable": execution.rollback_available,
        "rollbackTokenHash": str(execution.rollback_token_hash) if execution.rollback_token_hash else None,
        "error": _error_to_dict(execution.error),
    }


def execution_from_dict(data: Mapping[str, Any]) -> OperationExecution:
    return OperationExecution(
        execution_id=ExecutionId(data["executionId"]),
        plan_id=PlanId(data["planId"]),
        plan_version=int(data["planVersion"]),
        status=ExecutionStatus(data["status"]),
        executor_type=ExecutorType(data["executorType"]),
        executor_instance_id=Identifier(data["executorInstanceId"]),
        started_at=datetime.fromisoformat(data["startedAt"]),
        completed_at=_dt(data.get("completedAt")),
        operation_results=[operation_result_from_dict(item) for item in data.get("operationResults", [])],
        rollback_available=bool(data.get("rollbackAvailable", False)),
        rollback_token_hash=_hash(data.get("rollbackTokenHash")),
        error=_error_from_dict(data.get("error")),
    )


def operation_result_to_dict(result: OperationResult) -> dict[str, Any]:
    return {
        "operationId": str(result.operation_id),
        "status": result.status.value,
        "attemptCount": result.attempt_count,
        "beforeVersionHash": str(result.before_version_hash) if result.before_version_hash else None,
        "afterVersionHash": str(result.after_version_hash) if result.after_version_hash else None,
        "affectedPaths": [str(item) for item in result.affected_paths],
        "rollbackAvailable": result.rollback_available,
        "rollbackDataRef": result.rollback_data_ref,
        "error": _error_to_dict(result.error),
        "metadata": dict(result.metadata),
    }


def operation_result_from_dict(data: Mapping[str, Any]) -> OperationResult:
    return OperationResult(
        operation_id=OperationId(data["operationId"]),
        status=OperationResultStatus(data["status"]),
        attempt_count=int(data.get("attemptCount", 1)),
        before_version_hash=_hash(data.get("beforeVersionHash")),
        after_version_hash=_hash(data.get("afterVersionHash")),
        affected_paths=tuple(VaultPath(item) for item in data.get("affectedPaths", [])),
        rollback_available=bool(data.get("rollbackAvailable", False)),
        rollback_data_ref=data.get("rollbackDataRef"),
        error=_error_from_dict(data.get("error")),
        metadata=dict(data.get("metadata", {})),
    )


def _operation_to_dict(operation: KnowledgeOperation) -> dict[str, Any]:
    return {
        "operationId": str(operation.operation_id),
        "type": operation.operation_type.value,
        "order": operation.order,
        "description": operation.description,
        "reason": operation.reason,
        "riskLevel": operation.risk_level.contract_value,
        "input": _input_to_dict(operation),
        "dependsOn": [str(item) for item in operation.depends_on],
        "preconditions": [
            {
                "type": item.type.value,
                "target": item.target,
                "expected": item.expected,
                "failureCode": item.failure_code,
            }
            for item in operation.preconditions
        ],
        "expectedResult": _expected_to_dict(operation.expected_result),
        "idempotencyKey": str(operation.idempotency_key),
        "timeoutMs": operation.timeout_ms,
        "extensions": dict(operation.extensions),
    }


def _operation_from_dict(data: Mapping[str, Any]) -> KnowledgeOperation:
    operation_type = OperationType(data["type"])
    return KnowledgeOperation(
        operation_id=OperationId(data["operationId"]),
        operation_type=operation_type,
        order=int(data["order"]),
        description=data["description"],
        reason=data["reason"],
        risk_level=RiskLevel[data["riskLevel"].upper()],
        input=_input_from_dict(operation_type, data["input"]),
        depends_on=tuple(OperationId(item) for item in data.get("dependsOn", [])),
        preconditions=tuple(
            OperationPrecondition(
                type=PreconditionType(item["type"]),
                target=item["target"],
                expected=item.get("expected"),
                failure_code=item.get("failureCode"),
            )
            for item in data.get("preconditions", [])
        ),
        expected_result=_expected_from_dict(data.get("expectedResult")),
        idempotency_key=IdempotencyKey(data["idempotencyKey"]),
        timeout_ms=data.get("timeoutMs"),
        extensions=dict(data.get("extensions", {})),
    )


def _input_to_dict(operation: KnowledgeOperation) -> dict[str, Any]:
    value = operation.input
    if isinstance(value, CreateNoteInput):
        return {"path": str(value.path), "content": value.content, "conflictPolicy": value.conflict_policy.value, "frontmatter": dict(value.frontmatter)}
    if isinstance(value, UpdateNoteInput):
        return {
            "path": str(value.path), "expectedVersionHash": str(value.expected_version_hash),
            "updateMode": value.update_mode.value,
            "patches": [{"patchId": str(item.patch_id), "startOffset": item.start_offset, "endOffset": item.end_offset, "oldTextHash": str(item.old_text_hash), "newText": item.new_text, "contextBefore": item.context_before, "contextAfter": item.context_after} for item in value.patches],
            "section": None if value.section is None else {"headingPath": list(value.section.heading_path), "expectedSectionHash": str(value.section.expected_section_hash), "newContent": value.section.new_content, "includeHeading": value.section.include_heading},
            "content": value.content, "preserveFrontmatter": value.preserve_frontmatter,
        }
    if isinstance(value, MoveNoteInput):
        return {"sourcePath": str(value.source_path), "targetPath": str(value.target_path), "expectedVersionHash": str(value.expected_version_hash), "updateLinks": value.update_links, "conflictPolicy": value.conflict_policy.value}
    if isinstance(value, UpdateMetadataInput):
        return {"path": str(value.path), "expectedMetadataHash": str(value.expected_metadata_hash), "set": dict(value.set_values), "remove": list(value.remove_keys), "addTags": [str(item) for item in value.add_tags], "removeTags": [str(item) for item in value.remove_tags]}
    if isinstance(value, CreateTaskInput):
        return {"title": value.title, "targetPath": str(value.target_path), "priority": value.priority, "status": value.status, "description": value.description, "project": value.project, "dueDate": value.due_date.isoformat() if value.due_date else None, "tags": [str(item) for item in value.tags], "sourceNotePath": str(value.source_note_path) if value.source_note_path else None}
    if isinstance(value, InvokePluginInput):
        return {"pluginId": value.plugin_id, "capability": str(value.capability), "parameters": dict(value.parameters), "requiresPluginVersion": value.requires_plugin_version}
    raise TypeError(type(value))


def _input_from_dict(operation_type: OperationType, data: Mapping[str, Any]):
    if operation_type is OperationType.CREATE_NOTE:
        return CreateNoteInput(path=VaultPath(data["path"]), content=data["content"], conflict_policy=ConflictPolicy(data.get("conflictPolicy", "fail")), frontmatter=dict(data.get("frontmatter", {})))
    if operation_type is OperationType.UPDATE_NOTE:
        section_data = data.get("section")
        return UpdateNoteInput(
            path=VaultPath(data["path"]), expected_version_hash=Sha256Hash(data["expectedVersionHash"]), update_mode=UpdateMode(data["updateMode"]),
            patches=tuple(TextPatch(patch_id=Identifier(item["patchId"]), start_offset=int(item["startOffset"]), end_offset=int(item["endOffset"]), old_text_hash=Sha256Hash(item["oldTextHash"]), new_text=item["newText"], context_before=item.get("contextBefore"), context_after=item.get("contextAfter")) for item in data.get("patches", [])),
            section=None if section_data is None else SectionReplacement(heading_path=tuple(section_data["headingPath"]), expected_section_hash=Sha256Hash(section_data["expectedSectionHash"]), new_content=section_data["newContent"], include_heading=bool(section_data.get("includeHeading", False))),
            content=data.get("content"), preserve_frontmatter=bool(data.get("preserveFrontmatter", True)),
        )
    if operation_type is OperationType.MOVE_NOTE:
        return MoveNoteInput(source_path=VaultPath(data["sourcePath"]), target_path=VaultPath(data["targetPath"]), expected_version_hash=Sha256Hash(data["expectedVersionHash"]), update_links=bool(data.get("updateLinks", True)), conflict_policy=ConflictPolicy(data.get("conflictPolicy", "fail")))
    if operation_type is OperationType.UPDATE_METADATA:
        return UpdateMetadataInput(path=VaultPath(data["path"]), expected_metadata_hash=Sha256Hash(data["expectedMetadataHash"]), set_values=dict(data.get("set", {})), remove_keys=tuple(data.get("remove", [])), add_tags=tuple(TagName(item) for item in data.get("addTags", [])), remove_tags=tuple(TagName(item) for item in data.get("removeTags", [])))
    if operation_type is OperationType.CREATE_TASK:
        return CreateTaskInput(title=data["title"], target_path=VaultPath(data["targetPath"]), priority=data.get("priority", "medium"), status=data.get("status", "todo"), description=data.get("description", ""), project=data.get("project"), due_date=_dt(data.get("dueDate")), tags=tuple(TagName(item) for item in data.get("tags", [])), source_note_path=VaultPath(data["sourceNotePath"]) if data.get("sourceNotePath") else None)
    if operation_type is OperationType.INVOKE_PLUGIN:
        return InvokePluginInput(plugin_id=data["pluginId"], capability=CapabilityId(data["capability"]), parameters=dict(data.get("parameters", {})), requires_plugin_version=data.get("requiresPluginVersion"))
    raise TypeError(operation_type)


def _resource_to_dict(item: AffectedResource) -> dict[str, Any]:
    return {"resourceType": item.resource_type.value, "action": item.action.value, "resourceId": str(item.resource_id) if item.resource_id else None, "path": str(item.path) if item.path else None, "optional": item.optional}


def _resource_from_dict(data: Mapping[str, Any]) -> AffectedResource:
    return AffectedResource(resource_type=ResourceType(data["resourceType"]), action=ResourceAction(data["action"]), resource_id=Identifier(data["resourceId"]) if data.get("resourceId") else None, path=VaultPath(data["path"]) if data.get("path") else None, optional=bool(data.get("optional", False)))


def _warning_to_dict(item: OperationWarning) -> dict[str, Any]:
    return {"code": item.code, "message": item.message, "severity": item.severity.value, "operationIds": [str(value) for value in item.operation_ids], "paths": [str(value) for value in item.paths]}


def _warning_from_dict(data: Mapping[str, Any]) -> OperationWarning:
    return OperationWarning(code=data["code"], message=data["message"], severity=WarningSeverity(data["severity"]), operation_ids=tuple(OperationId(item) for item in data.get("operationIds", [])), paths=tuple(VaultPath(item) for item in data.get("paths", [])))


def _confirmation_to_dict(item: ConfirmationPolicy | None):
    if item is None:
        return None
    return {"mode": item.mode.value, "approverTypes": [value.value for value in item.approver_types], "expiresAt": item.expires_at.isoformat() if item.expires_at else None, "reason": item.reason}


def _confirmation_from_dict(data):
    if data is None:
        return None
    return ConfirmationPolicy(mode=ConfirmationMode(data["mode"]), approver_types=tuple(ApproverType(item) for item in data["approverTypes"]), expires_at=_dt(data.get("expiresAt")), reason=data.get("reason"))


def _approval_to_dict(item: PlanApproval | None):
    if item is None:
        return None
    return {"approverType": item.approver_type.value, "approverId": str(item.approver_id), "approvedOperationIds": [str(value) for value in item.approved_operation_ids], "approvedAt": item.approved_at.isoformat()}


def _approval_from_dict(data):
    if data is None:
        return None
    return PlanApproval(approver_type=ApproverType(data["approverType"]), approver_id=Identifier(data["approverId"]), approved_operation_ids=tuple(OperationId(item) for item in data["approvedOperationIds"]), approved_at=datetime.fromisoformat(data["approvedAt"]))


def _expected_to_dict(item: ExpectedResult | None):
    if item is None:
        return None
    return {"affectedPaths": [str(value) for value in item.affected_paths], "resultDescription": item.result_description, "expectedVersionHash": str(item.expected_version_hash) if item.expected_version_hash else None}


def _expected_from_dict(data):
    if data is None:
        return None
    return ExpectedResult(affected_paths=tuple(VaultPath(item) for item in data.get("affectedPaths", [])), result_description=data.get("resultDescription"), expected_version_hash=_hash(data.get("expectedVersionHash")))


def _error_to_dict(error: OperationError | None):
    if error is None:
        return None
    return {"code": error.code, "message": error.message, "retryable": error.retryable, "details": dict(error.details)}


def _error_from_dict(data):
    if data is None:
        return None
    return OperationError(code=data["code"], message=data["message"], retryable=bool(data.get("retryable", False)), details=dict(data.get("details", {})))
