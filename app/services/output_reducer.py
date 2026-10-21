"""Turns agent results into evidence text and numbered citation references.

This is the reduction hook of the graph: today it renders tool outputs as text and truncates oversized
ones; plug JSON-to-text conversion and map-reduce summarisation in here when outputs outgrow the cap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.components.resilience import CIRCUIT_OPEN, ErrorKind
from app.config import settings
from app.constants import AGENT_DISPLAY_NAMES, AGENT_ORDER, SOURCE_REF_TOKEN
from app.schemas.agent_io import AgentResult
from app.schemas.citations import CitationChunk
from app.services.citations import SOURCE_REF_TOKEN_PATTERN, ReferenceManager, strip_source_ref_tokens
from app.utils.logger import logger


@dataclass
class ReducedEvidence:
    evidence: dict[str, str] = field(default_factory=dict)
    references: list[dict[str, Any]] = field(default_factory=list)


# How a failed call is labelled in the evidence, so the sufficiency check can tell what another round could fix.
_FAILURE_LABELS = {
    ErrorKind.RETRYABLE.value: "temporarily unavailable, retries exhausted",
    CIRCUIT_OPEN: "temporarily unavailable, service skipped",
    ErrorKind.NON_RETRYABLE.value: "rejected",
}


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _apply_markers(text: str, token_markers: dict[str, str]) -> str:
    """Swap source tokens for their [sourceN] markers and drop tokens with no registered source."""
    for token, marker in token_markers.items():
        text = text.replace(token, marker)
    return strip_source_ref_tokens(text) if SOURCE_REF_TOKEN_PATTERN.search(text) else text


def reduce_agent_results(results: dict[str, AgentResult]) -> ReducedEvidence:
    manager = ReferenceManager(start_index=1)
    limit = settings.AGENT_EVIDENCE_MAX_CHARS_PER_SOURCE
    evidence: dict[str, str] = {}

    for agent in sorted(results, key=AGENT_ORDER.index):
        result = results[agent]
        # Every source becomes a static chunk with a stable [sourceN] marker; tokens the tool placed in its text
        # (for example the CLM answer's citations) are swapped for those markers wherever the text appears.
        token_markers: dict[str, str] = {}
        tool_lines: list[str] = []
        tool_contents: list[str] = []
        failures: list[str] = []
        for record in result.tool_calls:
            if record.output is None:
                failures.append(f"- {record.tool_name} [{_FAILURE_LABELS.get(record.error_kind or '', 'failed')}]: {record.error}")
                continue
            if record.no_data:
                tool_lines.append(f"- ({record.tool_name}) no data: the call succeeded but the source returned nothing")
                continue
            try:
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
                for source, ref in zip(record.output.sources, registered):
                    token_markers[SOURCE_REF_TOKEN.format(source_id=source.source_id)] = ref.marker
                content = _apply_markers(record.output.content, token_markers)
            except Exception:  # one malformed output must not lose the rest of the evidence
                logger.warning("[REDUCE] output_skipped agent=%s tool=%s", agent, record.tool_name, exc_info=True)
                failures.append(f"- {record.tool_name} [unreadable]: the output could not be processed and was skipped")
                continue
            tool_contents.append(content.strip())
            if len(content) > limit:
                logger.warning(
                    "[REDUCE] evidence_truncated agent=%s tool=%s chars=%s limit=%s (AGENT_EVIDENCE_MAX_CHARS_PER_SOURCE)",
                    agent, record.tool_name, len(content), limit,
                )
            tool_markers = " ".join(ref.marker for ref in registered)
            prefix = f"- {tool_markers} " if tool_markers else "- "
            tool_lines.append(f"{prefix}({record.tool_name}) {_truncate(content, limit)}")

        summary = _apply_markers(result.summary, token_markers).strip()
        if summary and summary in tool_contents:  # single-call agents return the tool text as their findings
            findings = "(same as the tool evidence below)"
        else:
            findings = _truncate(summary, limit) or "(none)"
        lines = [
            f"### {AGENT_DISPLAY_NAMES.get(agent, agent)} agent (status: {result.status}, attempt {result.attempt})",
            f"Agent findings: {findings}",
            "Tool evidence:",
            *(tool_lines or ["- (none)"]),
        ]
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
