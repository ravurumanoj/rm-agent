"""This app's wiring of app.observability: config from settings and the RM-specific signals it records.

Generic tracing/logging lives in app.observability (portable); this module only knows about this app's
settings, graph state, decisions and quality signals.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from opentelemetry import trace
from opentelemetry.trace import Span

from app.components.react.models import ToolCallRecord
from app.config import settings
from app.constants import (
    INTENT_OUT_OF_SCOPE,
    INTENT_UNSAFE,
    OUTCOME_DEGRADED,
    VERDICT_PARTIAL,
)
from app.observability import (
    BACKEND_ARIZE,
    BACKEND_OTLP,
    Attr,
    ObservabilityConfig,
    get_audit_logger,
    record_output,
    record_tool_result,
    set_attributes,
    setup_observability,
)
from app.observability import shutdown_observability as _shutdown_observability

_LOCAL_HOSTS = {"127.0.0.1", "localhost"}
_CITATION_MARKER = re.compile(r"\[source\d+\]|<sup>\s*\d+\s*</sup>", re.IGNORECASE)
_HEADING_OR_RULE = re.compile(r"^\s*(#|\||>?\s*$|-{3,})")


# --- setup ---------------------------------------------------------------------------------------------------


def observability_config() -> ObservabilityConfig:
    """PHOENIX_LOCAL_MODE=true: local Phoenix over OTLP. false: Arize AX with space id and API key."""
    local = settings.PHOENIX_LOCAL_MODE
    return ObservabilityConfig(
        enabled=settings.PHOENIX_ENABLED,
        service_name=settings.APP_NAME,
        service_version=settings.VERSION,
        project_name=settings.PHOENIX_PROJECT_NAME,
        backend=BACKEND_OTLP if local else BACKEND_ARIZE,
        endpoint=settings.PHOENIX_OTLP_ENDPOINT if local else _arize_endpoint(),
        space_id=settings.PHOENIX_SPACE_ID.strip(),
        api_key=settings.PHOENIX_API_KEY.strip(),
        certificate_file=settings.SSL_CA_CERT_PATH.strip(),
        capture_content=settings.PHOENIX_CAPTURE_MESSAGE_CONTENT,
        llm_provider_label=settings.PHOENIX_LLM_PROVIDER.strip(),
        tiktoken_estimates=settings.PHOENIX_TIKTOKEN_ESTIMATE,
        instrument_langchain=settings.PHOENIX_LANGCHAIN_AUTO_INSTRUMENT,
    )


def configure_observability() -> None:
    setup_observability(observability_config())


def shutdown_observability() -> None:
    _shutdown_observability()


def _arize_endpoint() -> str:
    """The deployed endpoint; a local PHOENIX_OTLP_ENDPOINT is never sent to Arize (empty = Arize default)."""
    endpoint = settings.PHOENIX_DEPLOYED_OTLP_ENDPOINT.strip() or settings.PHOENIX_OTLP_ENDPOINT.strip()
    return "" if (urlparse(endpoint).hostname or "") in _LOCAL_HOSTS else endpoint


# --- agent decisions -----------------------------------------------------------------------------------------


def record_routing_decision(phase: str, decision: Any, span: Span | None = None) -> None:
    """What the orchestrator decided: phase, intent, planned agents and execution mode (default: current span)."""
    plan = getattr(decision, "plan", None)
    set_attributes(
        span or trace.get_current_span(),
        {
            Attr.DECISION_PHASE: phase,
            Attr.DECISION_INTENT: decision.intent,
            Attr.DECISION_AGENTS: list(plan.agents) if plan else [],
            Attr.DECISION_EXECUTION_MODE: plan.mode if plan else None,
        },
    )


def record_sufficiency_verdict(verdict: Any, replan_count: int, span: Span | None = None) -> None:
    """The sufficiency check's verdict and issue counts (default: current span)."""
    set_attributes(
        span or trace.get_current_span(),
        {
            Attr.SUFFICIENCY_VERDICT: verdict.verdict,
            Attr.REPLAN_COUNT: replan_count,
            "app.sufficiency.grounding_issue_count": len(verdict.grounding_issues),
            "app.sufficiency.guardrail_issue_count": len(verdict.guardrail_issues),
            "app.sufficiency.data_gap_count": len(verdict.data_gaps),
        },
    )


def record_tool_call(span: Span, record: ToolCallRecord) -> None:
    """Map one ReAct/external tool call record onto its TOOL span."""
    record_tool_result(
        span,
        output=record.output.content if record.output else None,
        error=record.error,
        error_kind=record.error_kind,
        attempts=record.attempts,
        no_data=record.no_data,
    )


# --- query outcome, quality signals and audit -------------------------------------------------------------------


def citation_coverage(answer: str) -> float | None:
    """Share of statements (sentences, bullets, table rows aside) that carry at least one citation marker."""
    statements = [
        part.strip()
        for line in answer.splitlines()
        if line.strip() and not _HEADING_OR_RULE.match(line)
        for part in re.split(r"(?<=[.!?])\s+", line)
        if len(part.strip()) > 20
    ]
    if not statements:
        return None
    return round(sum(bool(_CITATION_MARKER.search(part)) for part in statements) / len(statements), 3)


def query_signals(final_state: dict[str, Any]) -> dict[str, Any]:
    """Outcome, reliability and quality signals of one query, as span attributes and audit fields."""
    decision = final_state.get("decision")
    verdict = final_state.get("sufficiency")
    results = (final_state.get("agent_results") or {}).values()
    records = [record for result in results for record in result.tool_calls]
    intent = getattr(decision, "intent", None)
    answer = final_state.get("final_output", "")
    return {
        Attr.OUTCOME: final_state.get("outcome", ""),
        Attr.PARTIAL_ANSWER: bool(
            final_state.get("outcome") == OUTCOME_DEGRADED or (verdict is not None and verdict.verdict == VERDICT_PARTIAL)
        ),
        Attr.DECISION_INTENT: intent,
        Attr.DECISION_AGENTS: list(final_state["plan"].agents) if final_state.get("plan") else [],
        Attr.REPLAN_COUNT: final_state.get("replan_count", 0),
        Attr.TOOL_CALL_COUNT: len(records),
        Attr.TOOL_FAILURE_COUNT: sum(not record.succeeded for record in records),
        Attr.RETRY_COUNT: sum(max(0, record.attempts - 1) for record in records),
        Attr.MAP_REDUCE_COUNT: sum(record.map_reduced for record in records)
        + int(final_state.get("evidence_map_reductions") or 0),
        Attr.CITATION_COUNT: len(final_state.get("citations") or []),
        Attr.CITATION_COVERAGE: citation_coverage(answer) if final_state.get("citations") else None,
        Attr.GUARDRAIL_TRIGGERED: intent == INTENT_UNSAFE or bool(verdict is not None and verdict.guardrail_issues),
        Attr.OUT_OF_SCOPE: intent == INTENT_OUT_OF_SCOPE,
    }


def record_query_outcome(span: Span, final_state: dict[str, Any]) -> dict[str, Any]:
    signals = query_signals(final_state)
    set_attributes(span, signals)
    record_output(span, final_state.get("final_output", ""))
    return signals


def audit_query(*, question: str, final_state: dict[str, Any], signals: dict[str, Any]) -> None:
    """One compliance audit record per query (separate log stream; restricted index in ELK)."""
    get_audit_logger().info(
        "query_completed",
        extra={
            "question": question,
            "answer": final_state.get("final_output", ""),
            "cited_sources": [
                {"title": item.get("title"), "uri": item.get("uri"), "source_id": item.get("content_id")}
                for item in final_state.get("citations") or []
            ],
            "node_trace": final_state.get("node_trace", []),
            **{key.removeprefix("app."): value for key, value in signals.items()},
        },
    )
