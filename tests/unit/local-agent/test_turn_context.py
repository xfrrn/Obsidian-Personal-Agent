from __future__ import annotations

import asyncio

from agent.core.context_injection.obsidian import ObsidianContextContributor
from agent.core.turn.context import TurnContext


def test_obsidian_turn_context_is_read_only_and_bounded() -> None:
    context = TurnContext(
        1,
        "question",
        "system",
        1,
        metadata={
            "scope": "current",
            "activeFilePath": "Current.md",
            "referencedPaths": ["Reference.md"],
            "selectedText": "x" * 9_000,
            "apiKey": "must-not-leak",
        },
    )
    rendered = asyncio.run(ObsidianContextContributor().contribute(context))
    assert rendered is not None
    assert '"activeFilePath": "Current.md"' in rendered
    assert '"referencedPaths": ["Reference.md"]' in rendered
    assert "must-not-leak" not in rendered
    assert "x" * 8_001 not in rendered
