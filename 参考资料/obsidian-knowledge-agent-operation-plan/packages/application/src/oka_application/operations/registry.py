from __future__ import annotations

from oka_domain.operations import OperationType

from .errors import OperationTypeUnsupported
from .ports import OperationHandler


class OperationHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[OperationType, OperationHandler] = {}

    def register(self, operation_type: OperationType, handler: OperationHandler) -> None:
        if operation_type in self._handlers:
            raise ValueError(f"Handler already registered for {operation_type.value}")
        self._handlers[operation_type] = handler

    def replace(self, operation_type: OperationType, handler: OperationHandler) -> None:
        self._handlers[operation_type] = handler

    def resolve(self, operation_type: OperationType) -> OperationHandler:
        try:
            return self._handlers[operation_type]
        except KeyError as exc:
            raise OperationTypeUnsupported(
                f"No handler registered for {operation_type.value}",
                details={"operationType": operation_type.value},
            ) from exc

    @property
    def supported_types(self) -> frozenset[OperationType]:
        return frozenset(self._handlers)
