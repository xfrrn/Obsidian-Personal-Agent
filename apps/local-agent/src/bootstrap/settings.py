"""Local Agent settings."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class LocalAgentSettings:
    host: str
    port: int
    vault_root: Path | None = None
    execution_mode: str = "confirm_all"


def load_settings() -> LocalAgentSettings:
    """Load local-agent settings from environment variables."""
    vault_root = os.environ.get("OBSIDIAN_AGENT_VAULT_ROOT")
    return LocalAgentSettings(
        host=os.environ.get("OBSIDIAN_AGENT_HOST", "127.0.0.1"),
        port=int(os.environ.get("OBSIDIAN_AGENT_PORT", "8765")),
        vault_root=Path(vault_root).expanduser().resolve() if vault_root else None,
        execution_mode=os.environ.get("OBSIDIAN_AGENT_EXECUTION_MODE", "confirm_all"),
    )
