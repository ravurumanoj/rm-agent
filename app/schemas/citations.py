from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CitationChunk:
    """Canonical chunk representation used by citation services."""

    content_id: str
    text: str
    title: str = ""
    uri: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CitationReference:
    """Resolved source reference used by API responses and UI citations."""

    source_number: int
    marker: str
    content_id: str
    title: str
    snippet: str
    uri: str
    metadata: dict[str, Any]


@dataclass
class SSEEvent:
    id: str
    event: str
    data: dict[str, Any]
    chat_id: str
    timestamp: float
