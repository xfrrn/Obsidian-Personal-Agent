from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class OperationApplicationError(Exception):
    """Base class for application-layer operation failures."""

    code = "OPERATION_APPLICATION_ERROR"
    retryable = False

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})


class OperationSafetyError(OperationApplicationError):
    code = "OPERATION_SAFETY_VALIDATION_FAILED"


class OperationConfirmationError(OperationApplicationError):
    code = "OPERATION_CONFIRMATION_INVALID"


class OperationExecutionLocked(OperationApplicationError):
    code = "OPERATION_EXECUTION_LOCKED"
    retryable = True


class OperationExecutorUnavailable(OperationApplicationError):
    code = "OPERATION_EXECUTOR_UNAVAILABLE"
    retryable = True


class OperationPreconditionFailed(OperationApplicationError):
    code = "OPERATION_PRECONDITION_FAILED"


class OperationPostconditionFailed(OperationApplicationError):
    code = "OPERATION_POSTCONDITION_FAILED"


class OperationVersionConflict(OperationApplicationError):
    code = "NOTE_VERSION_CONFLICT"


class OperationPathConflict(OperationApplicationError):
    code = "NOTE_PATH_CONFLICT"


class OperationPermissionDenied(OperationApplicationError):
    code = "PERMISSION_DENIED"


class OperationTypeUnsupported(OperationApplicationError):
    code = "OPERATION_TYPE_UNSUPPORTED"


class OperationRollbackError(OperationApplicationError):
    code = "ROLLBACK_FAILED"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    operation_id: str | None = None
    path: str | None = None
    details: Mapping[str, Any] | None = None
