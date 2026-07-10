class DomainError(Exception):
    """Base exception for all domain rule violations."""


class ValidationError(DomainError):
    """Raised when a value object or entity violates an invariant."""


class InvalidStateTransition(DomainError):
    """Raised when an aggregate cannot move to the requested state."""


class ConcurrencyConflict(DomainError):
    """Raised when an expected revision/hash does not match current state."""


class OperationPlanError(DomainError):
    """Raised when an operation plan is inconsistent or unsafe."""


class PermissionDenied(DomainError):
    """Raised when the current actor lacks a required capability."""
