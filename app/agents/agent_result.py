"""Builds the AgentResult every agent returns, so status rules live in one place."""

from __future__ import annotations

from app.components.react.models import ReActStep, SourceItem, ToolCallRecord
from app.constants import AGENT_STATUS_ERROR, AGENT_STATUS_NO_DATA, AGENT_STATUS_OK, AGENT_STATUS_PARTIAL
from app.schemas.agent_io import AgentResult, AgentTask


def _status(records: list[ToolCallRecord], error: str | None) -> str:
    if not records:
        return AGENT_STATUS_ERROR if error else AGENT_STATUS_NO_DATA
    succeeded = sum(record.succeeded for record in records)
    if succeeded == 0:
        return AGENT_STATUS_ERROR
    return AGENT_STATUS_PARTIAL if error or succeeded < len(records) else AGENT_STATUS_OK


def build_agent_result(
    task: AgentTask,
    *,
    records: list[ToolCallRecord],
    summary: str,
    terminated_reason: str,
    steps: list[ReActStep] | None = None,
    iterations: int = 0,
    error: str | None = None,
) -> AgentResult:
    sources: dict[str, SourceItem] = {}
    for record in records:
        for source in record.output.sources if record.output else []:
            sources.setdefault(source.source_id, source)

    return AgentResult(
        agent=task.agent,
        attempt=task.attempt,
        status=_status(records, error),
        summary=summary.strip(),
        tool_calls=records,
        steps=steps or [],
        sources=list(sources.values()),
        iterations=iterations,
        terminated_reason=terminated_reason,
        error=error,
    )
