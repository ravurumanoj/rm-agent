"""Shared building blocks for tool providers and the placeholder implementations."""

from __future__ import annotations

import hashlib
from typing import Any, Protocol

from langchain_core.tools import BaseTool

from app.components.react.models import SourceItem, ToolOutput

_WORDS = (
    "client", "portfolio", "allocation", "review", "update", "summary", "quarter", "equity", "fixed", "income",
    "risk", "profile", "balanced", "growth", "discussion", "follow", "action", "noted", "pending", "stable",
    "trend", "benchmark", "mandate", "advisor", "feedback", "position", "exposure", "currency", "liquidity", "plan",
)


class ToolProvider(Protocol):
    """Supplies the tools a ReAct sub-agent may call. Swap the implementation to go live."""

    def get_tools(self) -> list[BaseTool]: ...


class ExternalAgentClient(Protocol):
    """Single-call client for an external agent (for example one reached through Unique)."""

    async def ask(self, query: str, metadata: dict[str, Any]) -> ToolOutput: ...


def dummy_text(seed: str, *, min_len: int = 100, max_len: int = 200) -> str:
    """Deterministic placeholder text whose length is always within [min_len, max_len]."""
    digest = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16)
    target = min_len + digest % (max_len - min_len + 1)
    words: list[str] = []
    cursor = digest
    while len(" ".join(words)) < target:
        words.append(_WORDS[cursor % len(_WORDS)])
        cursor = cursor // 7 + len(words)
    text = " ".join(words).capitalize()
    return text[: target - 1] + "."


def dummy_tool_output(seed: str, *, source_prefix: str, title: str) -> dict:
    """Build a placeholder ToolOutput payload (as a dict) with one source."""
    content = dummy_text(seed)
    source_id = f"{source_prefix}-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:8]}"
    output = ToolOutput(
        content=content,
        sources=[SourceItem(source_id=source_id, title=title, uri=f"placeholder://{source_id}", snippet=content[:120])],
    )
    return output.model_dump()
