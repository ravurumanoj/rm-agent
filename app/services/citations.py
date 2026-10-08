from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from app.schemas.citations import CitationChunk, CitationReference, ContentReference
from app.utils.logger import logger


SOURCE_MARKER_PATTERN = r"\[source(\d+)\]"
DEFAULT_REFERENCE_SOURCE = "rm-agent"


class ReferenceManager:
    """Track source chunks and build message references the way Unique's ReferenceManager does.

    Chunks come from tool output, never from the LLM: each gets a stable [sourceN] marker, the LLM only cites
    markers, and the reference details (name, url, source id) are looked up from the registered chunk.
    """

    def __init__(self, start_index: int = 1, marker_template: str = "source{n}") -> None:
        if start_index < 1:
            raise ValueError("start_index must be >= 1")
        self._next_index = start_index
        self._marker_template = marker_template
        self._references: list[CitationReference] = []

    @classmethod
    def from_citations(cls, citations: Iterable[dict[str, Any]]) -> "ReferenceManager":
        """Rebuild a manager from citation dicts that already carry their source numbers."""
        manager = cls()
        for item in citations:
            manager._references.append(
                CitationReference(
                    source_number=int(item["source_number"]),
                    marker=str(item["marker"]),
                    content_id=str(item.get("content_id", "")),
                    title=str(item.get("title", "")),
                    snippet=str(item.get("snippet", "")),
                    uri=str(item.get("uri", "")),
                    metadata=dict(item.get("metadata") or {}),
                )
            )
        return manager

    @property
    def references(self) -> list[CitationReference]:
        return list(self._references)

    def reset(self, start_index: int = 1) -> None:
        if start_index < 1:
            raise ValueError("start_index must be >= 1")
        self._next_index = start_index
        self._references.clear()

    def register_chunks(self, chunks: Iterable[CitationChunk], snippet_max_len: int = 280) -> list[CitationReference]:
        """Register chunks and return their references; a chunk seen before keeps its first marker."""
        by_content_id = {ref.content_id: ref for ref in self._references}
        registered: list[CitationReference] = []
        for chunk in chunks:
            existing = by_content_id.get(chunk.content_id)
            if existing is not None:
                registered.append(existing)
                continue

            marker = self._marker_template.format(n=self._next_index)
            snippet = (chunk.text or "").strip()
            if len(snippet) > snippet_max_len:
                snippet = snippet[: snippet_max_len - 3].rstrip() + "..."

            ref = CitationReference(
                source_number=self._next_index,
                marker=f"[{marker}]",
                content_id=chunk.content_id,
                title=chunk.title,
                snippet=snippet,
                uri=chunk.uri,
                metadata=dict(chunk.metadata),
            )
            self._references.append(ref)
            by_content_id[chunk.content_id] = ref
            registered.append(ref)
            self._next_index += 1
        logger.debug("[CITATION] registered_chunks=%s total_references=%s", len(registered), len(self._references))
        return registered

    def extract_cited_source_numbers(
        self,
        answer_text: str,
        pattern: str = SOURCE_MARKER_PATTERN,
    ) -> list[int]:
        """Extract cited source numbers from answer text."""
        if not answer_text:
            return []
        seen: set[int] = set()
        for match in re.findall(pattern, answer_text, flags=re.IGNORECASE):
            try:
                seen.add(int(match))
            except ValueError:
                continue
        return sorted(seen)

    def get_cited_references(
        self,
        answer_text: str,
        pattern: str = SOURCE_MARKER_PATTERN,
    ) -> list[CitationReference]:
        """Return only references that are actually cited in the answer text."""
        used = set(self.extract_cited_source_numbers(answer_text, pattern=pattern))
        if not used:
            return []
        return [ref for ref in self._references if ref.source_number in used]

    def build_reference_context(
        self,
        references: Optional[Iterable[CitationReference]] = None,
        include_snippet: bool = True,
    ) -> str:
        """Build compact model context for references."""
        refs = list(references) if references is not None else self._references
        lines: list[str] = []
        for ref in refs:
            if include_snippet:
                lines.append(f"{ref.marker} {ref.title or ref.content_id}: {ref.snippet}")
            else:
                lines.append(f"{ref.marker} {ref.title or ref.content_id}")
        return "\n".join(lines)

    def to_message_references(self, answer_text: str) -> tuple[str, list[ContentReference]]:
        """Turn [sourceN] markers into <sup>k</sup> and return the matching message references.

        k is the order of first appearance in the text, so the UI shows 1, 2, 3 whatever the marker numbers are.
        """
        by_number = {ref.source_number: ref for ref in self._references}
        sequence: dict[int, int] = {}
        references: list[ContentReference] = []

        def replace(match: re.Match[str]) -> str:
            number = int(match.group(1))
            ref = by_number.get(number)
            if ref is None:
                return ""
            if number not in sequence:
                sequence[number] = len(sequence) + 1
                references.append(
                    ContentReference(
                        name=ref.title or ref.content_id,
                        url=ref.uri,
                        sequence_number=sequence[number],
                        source_id=ref.content_id,
                        source=str(
                            ref.metadata.get("source")
                            or ref.metadata.get("tool")
                            or ref.metadata.get("agent")
                            or DEFAULT_REFERENCE_SOURCE
                        ),
                        original_index=[number],
                        description=ref.snippet,
                    )
                )
            return f"<sup>{sequence[number]}</sup>"

        text = re.sub(SOURCE_MARKER_PATTERN, replace, answer_text or "", flags=re.IGNORECASE)
        return text, references


def coerce_citation_chunk(raw: dict[str, Any]) -> CitationChunk:
    """Convert a raw tool/search payload into a CitationChunk."""
    content_id = str(raw.get("content_id") or raw.get("id") or "").strip()
    if not content_id:
        logger.debug("[CITATION] skipped_chunk_missing_content_id")
        raise ValueError("Citation chunk requires content_id or id")

    text = str(raw.get("text") or raw.get("content") or raw.get("snippet") or "").strip()
    title = str(raw.get("title") or raw.get("name") or "").strip()
    uri = str(raw.get("uri") or raw.get("url") or "").strip()
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}

    return CitationChunk(
        content_id=content_id,
        text=text,
        title=title,
        uri=uri,
        metadata=dict(metadata),
    )
