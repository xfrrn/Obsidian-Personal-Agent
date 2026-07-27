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
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None


def load_settings() -> LocalAgentSettings:
    """Load local-agent settings from environment variables."""
    vault_root = os.environ.get("OBSIDIAN_AGENT_VAULT_ROOT")
    return LocalAgentSettings(
        host=os.environ.get("OBSIDIAN_AGENT_HOST", "127.0.0.1"),
        port=int(os.environ.get("OBSIDIAN_AGENT_PORT", "8765")),
        vault_root=Path(vault_root).expanduser().resolve() if vault_root else None,
        execution_mode=os.environ.get("OBSIDIAN_AGENT_EXECUTION_MODE", "confirm_all"),
        llm_base_url=os.environ.get("OBSIDIAN_AGENT_LLM_BASE_URL"),
        llm_model=os.environ.get("OBSIDIAN_AGENT_LLM_MODEL"),
        llm_api_key=os.environ.get("OBSIDIAN_AGENT_LLM_API_KEY"),
    )
