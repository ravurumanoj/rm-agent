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


@dataclass(frozen=True)
class ContentReference:
    """Message reference in the shape Unique's Message.Reference expects."""

    name: str
    url: str
    sequence_number: int
    source_id: str
    source: str
    original_index: list[int]
    description: str = ""

    def to_unique(self) -> dict[str, Any]:
        reference: dict[str, Any] = {
            "name": self.name,
            "url": self.url or None,
            "sequenceNumber": self.sequence_number,
            "originalIndex": self.original_index,
            "sourceId": self.source_id,
            "source": self.source,
        }
        if self.description:
            reference["description"] = self.description
        return reference


@dataclass
class SSEEvent:
    id: str
    event: str
    data: dict[str, Any]
    chat_id: str
    timestamp: float
