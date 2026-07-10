"""Local Agent settings."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class LocalAgentSettings:
    host: str
    port: int
    vault_root: Path


def load_settings() -> LocalAgentSettings:
    """Load local-agent settings from environment variables."""
    vault_root = os.environ.get("OBSIDIAN_AGENT_VAULT_ROOT")
    if not vault_root:
        raise RuntimeError("OBSIDIAN_AGENT_VAULT_ROOT is required")
    return LocalAgentSettings(
        host=os.environ.get("OBSIDIAN_AGENT_HOST", "127.0.0.1"),
        port=int(os.environ.get("OBSIDIAN_AGENT_PORT", "8765")),
        vault_root=Path(vault_root).expanduser().resolve(),
    )
