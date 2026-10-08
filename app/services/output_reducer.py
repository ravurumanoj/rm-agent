"""Turns agent results into evidence text and numbered citation references.

This is the reduction hook of the graph: today it renders tool outputs as text and truncates oversized
ones; plug JSON-to-text conversion and map-reduce summarisation in here when outputs outgrow the cap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.constants import AGENT_DISPLAY_NAMES, AGENT_ORDER, SOURCE_REF_TOKEN
from app.schemas.agent_io import AgentResult
from app.schemas.citations import CitationChunk
from app.services.citations import ReferenceManager


@dataclass
class ReducedEvidence:
    evidence: dict[str, str] = field(default_factory=dict)
    references: list[dict[str, Any]] = field(default_factory=list)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def reduce_agent_results(results: dict[str, AgentResult]) -> ReducedEvidence:
    manager = ReferenceManager(start_index=1)
    limit = settings.AGENT_EVIDENCE_MAX_CHARS_PER_SOURCE
    evidence: dict[str, str] = {}

    for agent in sorted(results, key=AGENT_ORDER.index):
        result = results[agent]
        lines = [
            f"### {AGENT_DISPLAY_NAMES.get(agent, agent)} agent (status: {result.status}, attempt {result.attempt})",
            f"Agent findings: {_truncate(result.summary, limit) or '(none)'}",
            "Tool evidence:",
        ]
        failures: list[str] = []
        for record in result.tool_calls:
            if record.output is None:
                failures.append(f"- {record.tool_name}: {record.error}")
                continue
            registered = manager.register_chunks(
                CitationChunk(
                    content_id=source.source_id,
                    text=source.snippet or record.output.content,
                    title=source.title,
                    uri=source.uri,
                    metadata={"agent": agent, "tool": record.tool_name, "source": source.source},
                )
                for source in record.output.sources
            )
            content = record.output.content
            for source, ref in zip(record.output.sources, registered):
                content = content.replace(SOURCE_REF_TOKEN.format(source_id=source.source_id), ref.marker)
            tool_markers = " ".join(ref.marker for ref in registered)
            prefix = f"- {tool_markers} " if tool_markers else "- "
            lines.append(f"{prefix}({record.tool_name}) {_truncate(content, limit)}")
        if len(lines) == 3:
            lines.append("- (none)")
        if failures:
            lines.append("Failed calls:")
            lines.extend(failures)
        if result.error:
            lines.append(f"Agent error: {result.error}")
        evidence[agent] = "\n".join(lines)

    references = [
        {
            "source_number": ref.source_number,
            "marker": ref.marker,
            "content_id": ref.content_id,
            "title": ref.title,
            "snippet": ref.snippet,
            "uri": ref.uri,
            "metadata": ref.metadata,
        }
        for ref in manager.references
    ]
    return ReducedEvidence(evidence=evidence, references=references)
