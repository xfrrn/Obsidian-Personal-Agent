from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from oka_application.operations.errors import OperationExecutorUnavailable
from oka_domain.common import CapabilityId


PluginCallable = Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]
RollbackCallable = Callable[[Mapping[str, Any]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RegisteredPluginCapability:
    plugin_id: str
    capability: CapabilityId
    invoke: PluginCallable
    rollback: RollbackCallable | None = None


class InMemoryPluginInvoker:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], RegisteredPluginCapability] = {}

    def register(self, item: RegisteredPluginCapability) -> None:
        self._items[(item.plugin_id, str(item.capability))] = item

    def available(self, plugin_id: str, capability: CapabilityId) -> bool:
        return (plugin_id, str(capability)) in self._items

    async def invoke(self, plugin_id: str, capability: CapabilityId, parameters):
        item = self._items.get((plugin_id, str(capability)))
        if item is None:
            raise OperationExecutorUnavailable(
                f"Plugin capability is unavailable: {plugin_id}:{capability}"
            )
        return await item.invoke(parameters)

    async def rollback(self, plugin_id: str, capability: CapabilityId, payload):
        item = self._items.get((plugin_id, str(capability)))
        if item is None or item.rollback is None:
            raise OperationExecutorUnavailable(
                f"Plugin rollback is unavailable: {plugin_id}:{capability}"
            )
        await item.rollback(payload)

    def supports_rollback(self, plugin_id: str, capability: CapabilityId) -> bool:
        item = self._items.get((plugin_id, str(capability)))
        return item is not None and item.rollback is not None
