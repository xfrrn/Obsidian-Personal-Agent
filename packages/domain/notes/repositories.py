"""Note repository ports."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence


class NoteRepository(Protocol):
    """Port for reading notes without depending on a concrete vault adapter."""

    async def get(self, path: str) -> Mapping[str, Any]:
        """Return one note by vault-relative path."""
        ...

    async def search(self, query: str, *, limit: int = 10) -> Sequence[Mapping[str, Any]]:
        """Return notes matching the user query."""
        ...
