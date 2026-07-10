from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Any, Mapping, Sequence

from oka_domain.common import (
    AggregateRoot,
    CapabilityId,
    Identifier,
    IdempotencyKey,
    OperationId,
    PlanId,
    ResourceType,
    Sha256Hash,
    TagName,
    VaultPath,
    sha256_of,
    utc_now,
)
from oka_domain.exceptions import InvalidStateTransition, OperationPlanError, ValidationError
from .events import OperationPlanConfirmed, OperationPlanCreated, OperationPlanStatusChanged


class RiskLevel(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @property
    def contract_value(self) -> str:
        return self.name.lower()


class PlanStatus(StrEnum):
    DRAFT = "draft"
    PENDING_CONFIRMATION = "pending-confirmation"
    CONFIRMED = "confirmed"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ROLLED_BACK = "rolled-back"


class FailurePolicy(StrEnum):
    STOP = "stop"
    CONTINUE = "continue"
    ROLLBACK_ALL = "rollback-all"


class RollbackMode(StrEnum):
    NONE = "none"
    BEST_EFFORT = "best-effort"
    REQUIRED = "required"


class ConfirmationMode(StrEnum):
    NONE = "none"
    EXPLICIT = "explicit"
    SELECTED_OPERATIONS = "selected-operations"


class ApproverType(StrEnum):
    LOCAL_USER = "local-user"
    REMOTE_USER = "remote-user"
    ADMINISTRATOR = "administrator"


class ActorType(StrEnum):
    USER = "user"
    AGENT = "agent"
    RULE = "rule"
    AUTOMATION = "automation"
    GATEWAY = "gateway"
    SYSTEM = "system"


class ResourceAction(StrEnum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    MOVE = "move"
    RENAME = "rename"
    DELETE = "delete"
    INVOKE = "invoke"


class WarningSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class PreconditionType(StrEnum):
    FILE_EXISTS = "file-exists"
    FILE_NOT_EXISTS = "file-not-exists"
    VERSION_MATCH = "version-match"
    CONTENT_HASH_MATCH = "content-hash-match"
    METADATA_HASH_MATCH = "metadata-hash-match"
    PLUGIN_AVAILABLE = "plugin-available"
    PERMISSION_GRANTED = "permission-granted"
    FOLDER_EXISTS = "folder-exists"


class ConflictPolicy(StrEnum):
    FAIL = "fail"
    RENAME = "rename"
    OVERWRITE = "overwrite"
    MERGE = "merge"


class UpdateMode(StrEnum):
    PATCH = "patch"
    REPLACE_SECTION = "replace-section"
    REPLACE_CONTENT = "replace-content"


class OperationType(StrEnum):
    CREATE_NOTE = "create-note"
    UPDATE_NOTE = "update-note"
    MOVE_NOTE = "move-note"
    UPDATE_METADATA = "update-metadata"
    CREATE_TASK = "create-task"
    INVOKE_PLUGIN = "invoke-plugin"


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationSource:
    actor_type: ActorType
    actor_id: Identifier
    remote: bool
    channel: str | None = None
    instruction_id: Identifier | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationPrecondition:
    type: PreconditionType
    target: str
    expected: Any = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if not self.target.strip():
            raise ValidationError("Precondition target cannot be blank")


@dataclass(frozen=True, slots=True, kw_only=True)
class ExpectedResult:
    affected_paths: tuple[VaultPath, ...] = ()
    result_description: str | None = None
    expected_version_hash: Sha256Hash | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AffectedResource:
    resource_type: ResourceType
    action: ResourceAction
    resource_id: Identifier | None = None
    path: VaultPath | None = None
    optional: bool = False

    def __post_init__(self) -> None:
        if self.resource_id is None and self.path is None:
            raise ValidationError("AffectedResource needs resource_id or path")


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationWarning:
    code: str
    message: str
    severity: WarningSeverity
    operation_ids: tuple[OperationId, ...] = ()
    paths: tuple[VaultPath, ...] = ()

    def __post_init__(self) -> None:
        if not self.code or not self.message.strip():
            raise ValidationError("Warning code and message cannot be blank")


@dataclass(frozen=True, slots=True, kw_only=True)
class RollbackPolicy:
    mode: RollbackMode
    snapshot_required: bool
    retention_seconds: int
    max_rollback_age_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.retention_seconds < 0:
            raise ValidationError("Rollback retention cannot be negative")
        if self.mode is RollbackMode.REQUIRED and not self.snapshot_required:
            raise ValidationError("Required rollback mode must require snapshots")


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfirmationPolicy:
    mode: ConfirmationMode
    approver_types: tuple[ApproverType, ...]
    expires_at: datetime | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.approver_types:
            raise ValidationError("Confirmation policy needs at least one approver type")


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanApproval:
    approver_type: ApproverType
    approver_id: Identifier
    approved_operation_ids: tuple[OperationId, ...]
    approved_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True, kw_only=True)
class TextPatch:
    patch_id: Identifier
    start_offset: int
    end_offset: int
    old_text_hash: Sha256Hash
    new_text: str
    context_before: str | None = None
    context_after: str | None = None

    def __post_init__(self) -> None:
        if self.start_offset < 0 or self.end_offset < self.start_offset:
            raise ValidationError("Text patch offsets are invalid")


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionReplacement:
    heading_path: tuple[str, ...]
    expected_section_hash: Sha256Hash
    new_content: str
    include_heading: bool = False

    def __post_init__(self) -> None:
        if not self.heading_path or any(not heading.strip() for heading in self.heading_path):
            raise ValidationError("Section replacement needs a non-empty heading path")


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateNoteInput:
    path: VaultPath
    content: str
    conflict_policy: ConflictPolicy = ConflictPolicy.FAIL
    frontmatter: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateNoteInput:
    path: VaultPath
    expected_version_hash: Sha256Hash
    update_mode: UpdateMode
    patches: tuple[TextPatch, ...] = ()
    section: SectionReplacement | None = None
    content: str | None = None
    preserve_frontmatter: bool = True

    def __post_init__(self) -> None:
        if self.update_mode is UpdateMode.PATCH and not self.patches:
            raise ValidationError("Patch mode needs at least one patch")
        if self.update_mode is UpdateMode.REPLACE_SECTION and self.section is None:
            raise ValidationError("Replace-section mode needs section data")
        if self.update_mode is UpdateMode.REPLACE_CONTENT and self.content is None:
            raise ValidationError("Replace-content mode needs content")
        sorted_patches = sorted(self.patches, key=lambda patch: patch.start_offset)
        for left, right in zip(sorted_patches, sorted_patches[1:]):
            if left.end_offset > right.start_offset:
                raise ValidationError("Text patches cannot overlap")


@dataclass(frozen=True, slots=True, kw_only=True)
class MoveNoteInput:
    source_path: VaultPath
    target_path: VaultPath
    expected_version_hash: Sha256Hash
    update_links: bool = True
    conflict_policy: ConflictPolicy = ConflictPolicy.FAIL

    def __post_init__(self) -> None:
        if self.source_path == self.target_path:
            raise ValidationError("Source and target path cannot be equal")
        if self.conflict_policy is ConflictPolicy.OVERWRITE:
            raise ValidationError("Move-note does not support overwrite conflict policy")


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateMetadataInput:
    path: VaultPath
    expected_metadata_hash: Sha256Hash
    set_values: Mapping[str, Any] = field(default_factory=dict)
    remove_keys: tuple[str, ...] = ()
    add_tags: tuple[TagName, ...] = ()
    remove_tags: tuple[TagName, ...] = ()

    def __post_init__(self) -> None:
        if not (self.set_values or self.remove_keys or self.add_tags or self.remove_tags):
            raise ValidationError("Update-metadata needs at least one change")
        if set(self.add_tags) & set(self.remove_tags):
            raise ValidationError("The same tag cannot be added and removed")


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateTaskInput:
    title: str
    target_path: VaultPath
    priority: str = "medium"
    status: str = "todo"
    description: str = ""
    project: str | None = None
    due_date: datetime | None = None
    tags: tuple[TagName, ...] = ()
    source_note_path: VaultPath | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValidationError("Task title cannot be blank")
        if self.priority not in {"low", "medium", "high", "urgent"}:
            raise ValidationError("Unsupported task priority")
        if self.status not in {"todo", "doing", "blocked", "done", "cancelled"}:
            raise ValidationError("Unsupported task status")


@dataclass(frozen=True, slots=True, kw_only=True)
class InvokePluginInput:
    plugin_id: str
    capability: CapabilityId
    parameters: Mapping[str, Any]
    requires_plugin_version: str | None = None

    def __post_init__(self) -> None:
        if not self.plugin_id.strip():
            raise ValidationError("Plugin id cannot be blank")


OperationInput = (
    CreateNoteInput
    | UpdateNoteInput
    | MoveNoteInput
    | UpdateMetadataInput
    | CreateTaskInput
    | InvokePluginInput
)


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeOperation:
    operation_id: OperationId
    operation_type: OperationType
    order: int
    description: str
    reason: str
    risk_level: RiskLevel
    input: OperationInput
    depends_on: tuple[OperationId, ...] = ()
    preconditions: tuple[OperationPrecondition, ...] = ()
    expected_result: ExpectedResult | None = None
    idempotency_key: IdempotencyKey
    timeout_ms: int | None = None
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.order < 0:
            raise ValidationError("Operation order cannot be negative")
        if not self.description.strip() or not self.reason.strip():
            raise ValidationError("Operation description and reason cannot be blank")
        if self.operation_id in self.depends_on:
            raise ValidationError("Operation cannot depend on itself")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValidationError("Operation dependencies must be unique")
        if self.timeout_ms is not None and not 100 <= self.timeout_ms <= 600_000:
            raise ValidationError("Operation timeout must be between 100 and 600000 ms")
        expected_type = {
            CreateNoteInput: OperationType.CREATE_NOTE,
            UpdateNoteInput: OperationType.UPDATE_NOTE,
            MoveNoteInput: OperationType.MOVE_NOTE,
            UpdateMetadataInput: OperationType.UPDATE_METADATA,
            CreateTaskInput: OperationType.CREATE_TASK,
            InvokePluginInput: OperationType.INVOKE_PLUGIN,
        }[type(self.input)]
        if self.operation_type is not expected_type:
            raise ValidationError(
                f"Operation type {self.operation_type} does not match input {type(self.input).__name__}"
            )


@dataclass(slots=True, kw_only=True)
class OperationPlan(AggregateRoot):
    plan_id: PlanId
    plan_version: int
    instruction: str
    summary: str
    source: OperationSource
    risk_level: RiskLevel
    status: PlanStatus
    requires_confirmation: bool
    operations: tuple[KnowledgeOperation, ...]
    affected_resources: tuple[AffectedResource, ...]
    warnings: tuple[OperationWarning, ...]
    rollback_policy: RollbackPolicy
    failure_policy: FailurePolicy
    idempotency_key: IdempotencyKey
    created_at: datetime = field(default_factory=utc_now)
    expires_at: datetime | None = None
    confirmation_policy: ConfirmationPolicy | None = None
    approval: PlanApproval | None = None
    integrity: Sha256Hash | None = None
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate_structure()
        if self.integrity is None:
            self.integrity = self.calculate_integrity()

    @classmethod
    def create(
        cls,
        *,
        instruction: str,
        summary: str,
        source: OperationSource,
        operations: Sequence[KnowledgeOperation],
        affected_resources: Sequence[AffectedResource],
        rollback_policy: RollbackPolicy,
        failure_policy: FailurePolicy,
        idempotency_key: IdempotencyKey,
        warnings: Sequence[OperationWarning] = (),
        confirmation_policy: ConfirmationPolicy | None = None,
        expires_at: datetime | None = None,
    ) -> OperationPlan:
        operation_tuple = tuple(sorted(operations, key=lambda operation: operation.order))
        risk_level = max((operation.risk_level for operation in operation_tuple), default=RiskLevel.LOW)
        requires_confirmation = risk_level >= RiskLevel.MEDIUM
        if requires_confirmation and confirmation_policy is None:
            confirmation_policy = ConfirmationPolicy(
                mode=ConfirmationMode.EXPLICIT,
                approver_types=(ApproverType.LOCAL_USER,),
            )
        if not requires_confirmation and confirmation_policy is None:
            confirmation_policy = ConfirmationPolicy(
                mode=ConfirmationMode.NONE,
                approver_types=(ApproverType.LOCAL_USER,),
            )
        status = (
            PlanStatus.PENDING_CONFIRMATION if requires_confirmation else PlanStatus.CONFIRMED
        )
        plan = cls(
            plan_id=PlanId.new(),
            plan_version=1,
            instruction=instruction,
            summary=summary,
            source=source,
            risk_level=risk_level,
            status=status,
            requires_confirmation=requires_confirmation,
            operations=operation_tuple,
            affected_resources=tuple(affected_resources),
            warnings=tuple(warnings),
            rollback_policy=rollback_policy,
            failure_policy=failure_policy,
            idempotency_key=idempotency_key,
            confirmation_policy=confirmation_policy,
            expires_at=expires_at,
        )
        plan._record(
            OperationPlanCreated(
                aggregate_id=str(plan.plan_id),
                payload={"operationCount": len(plan.operations), "riskLevel": risk_level.contract_value},
            )
        )
        return plan

    def calculate_integrity(self) -> Sha256Hash:
        return sha256_of(
            self,
            exclude_fields=frozenset({"integrity", "approval", "revision", "status"}),
        )

    def validate_integrity(self) -> None:
        if self.integrity != self.calculate_integrity():
            raise OperationPlanError("Operation plan integrity hash does not match its contents")

    def validate_structure(self) -> None:
        if self.plan_version < 1:
            raise OperationPlanError("Plan version must be at least 1")
        if not self.instruction.strip() or not self.summary.strip():
            raise OperationPlanError("Plan instruction and summary cannot be blank")
        if not self.operations:
            raise OperationPlanError("Operation plan needs at least one operation")
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise OperationPlanError("Plan expiry must be later than creation time")
        operation_ids = [operation.operation_id for operation in self.operations]
        if len(set(operation_ids)) != len(operation_ids):
            raise OperationPlanError("Operation ids must be unique")
        orders = [operation.order for operation in self.operations]
        if len(set(orders)) != len(orders):
            raise OperationPlanError("Operation orders must be unique")
        id_set = set(operation_ids)
        order_map = {operation.operation_id: operation.order for operation in self.operations}
        for operation in self.operations:
            missing = set(operation.depends_on) - id_set
            if missing:
                raise OperationPlanError(
                    f"Operation {operation.operation_id} depends on unknown ids: {missing}"
                )
            if any(order_map[dependency] >= operation.order for dependency in operation.depends_on):
                raise OperationPlanError("Dependencies must execute before dependent operations")
        self._ensure_acyclic()
        max_risk = max(operation.risk_level for operation in self.operations)
        if self.risk_level < max_risk:
            raise OperationPlanError("Plan risk cannot be lower than its highest-risk operation")
        if self.risk_level is RiskLevel.CRITICAL and not self.requires_confirmation:
            raise OperationPlanError("Critical plan must require confirmation")
        if self.requires_confirmation:
            if self.confirmation_policy is None:
                raise OperationPlanError("Confirmation policy is required")
            if self.confirmation_policy.mode is ConfirmationMode.NONE:
                raise OperationPlanError("Confirmation-required plan cannot use none mode")
        elif self.confirmation_policy and self.confirmation_policy.mode is not ConfirmationMode.NONE:
            raise OperationPlanError("Non-confirmation plan must use none confirmation mode")

    def confirm(
        self,
        *,
        approver_type: ApproverType,
        approver_id: Identifier,
        approved_operation_ids: Sequence[OperationId] | None = None,
        now: datetime | None = None,
    ) -> None:
        if self.status is not PlanStatus.PENDING_CONFIRMATION:
            raise InvalidStateTransition("Only pending plans can be confirmed")
        now = now or utc_now()
        self._ensure_not_expired(now)
        policy = self.confirmation_policy
        if policy is None or approver_type not in policy.approver_types:
            raise OperationPlanError("Approver type is not allowed by confirmation policy")
        if policy.expires_at and now >= policy.expires_at:
            raise OperationPlanError("Confirmation policy has expired")
        selected = tuple(approved_operation_ids or (operation.operation_id for operation in self.operations))
        operation_id_set = {operation.operation_id for operation in self.operations}
        if not set(selected) <= operation_id_set:
            raise OperationPlanError("Approval contains unknown operation ids")
        if policy.mode is ConfirmationMode.EXPLICIT and set(selected) != operation_id_set:
            raise OperationPlanError("Explicit confirmation must approve the whole plan")
        if policy.mode is ConfirmationMode.SELECTED_OPERATIONS:
            self._validate_selected_dependencies(set(selected))
        self.approval = PlanApproval(
            approver_type=approver_type,
            approver_id=approver_id,
            approved_operation_ids=selected,
            approved_at=now,
        )
        self._transition(PlanStatus.CONFIRMED)
        self._record(
            OperationPlanConfirmed(
                aggregate_id=str(self.plan_id),
                payload={"approvedOperationIds": [str(item) for item in selected]},
            )
        )

    def start_execution(self, *, now: datetime | None = None) -> None:
        now = now or utc_now()
        self._ensure_not_expired(now)
        self.validate_integrity()
        if self.status is not PlanStatus.CONFIRMED:
            raise InvalidStateTransition("Only confirmed plans can start execution")
        if self.requires_confirmation and self.approval is None:
            raise OperationPlanError("Plan requires an approval before execution")
        self._transition(PlanStatus.EXECUTING)

    def mark_completed(self) -> None:
        if self.status is not PlanStatus.EXECUTING:
            raise InvalidStateTransition("Only executing plans can complete")
        self._transition(PlanStatus.COMPLETED)

    def mark_failed(self) -> None:
        if self.status is not PlanStatus.EXECUTING:
            raise InvalidStateTransition("Only executing plans can fail")
        self._transition(PlanStatus.FAILED)

    def cancel(self) -> None:
        if self.status in {PlanStatus.COMPLETED, PlanStatus.ROLLED_BACK}:
            raise InvalidStateTransition("Completed or rolled-back plan cannot be cancelled")
        self._transition(PlanStatus.CANCELLED)

    def mark_rolled_back(self) -> None:
        if self.status not in {PlanStatus.COMPLETED, PlanStatus.FAILED}:
            raise InvalidStateTransition("Only completed or failed plans can be rolled back")
        self._transition(PlanStatus.ROLLED_BACK)

    def approved_operations(self) -> tuple[KnowledgeOperation, ...]:
        if not self.requires_confirmation:
            return self.operations
        if self.approval is None:
            return ()
        approved_ids = set(self.approval.approved_operation_ids)
        return tuple(operation for operation in self.operations if operation.operation_id in approved_ids)

    def _transition(self, status: PlanStatus) -> None:
        old_status = self.status
        self.status = status
        self.revision += 1
        self._record(
            OperationPlanStatusChanged(
                aggregate_id=str(self.plan_id),
                payload={"oldStatus": old_status.value, "newStatus": status.value},
            )
        )

    def _ensure_not_expired(self, now: datetime) -> None:
        if self.expires_at is not None and now >= self.expires_at:
            raise OperationPlanError("Operation plan has expired")

    def _ensure_acyclic(self) -> None:
        graph = {operation.operation_id: set(operation.depends_on) for operation in self.operations}
        visiting: set[OperationId] = set()
        visited: set[OperationId] = set()

        def visit(node: OperationId) -> None:
            if node in visiting:
                raise OperationPlanError("Operation dependency graph contains a cycle")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for operation_id in graph:
            visit(operation_id)

    def _validate_selected_dependencies(self, selected: set[OperationId]) -> None:
        operation_map = {operation.operation_id: operation for operation in self.operations}
        for operation_id in selected:
            missing = set(operation_map[operation_id].depends_on) - selected
            if missing:
                raise OperationPlanError(
                    f"Selected operation {operation_id} is missing dependencies: {missing}"
                )
