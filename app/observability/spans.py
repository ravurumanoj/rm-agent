"""Span helpers for workflow steps, agents and tool calls (LLM calls are in llm.py).

    with start_span(SpanName.node("orchestrator"), kind=SpanKind.CHAIN) as span:
        ...
        set_attributes(span, {Attr.DECISION_INTENT: "data_request"})

    with start_tool_span("get_portfolio_summary", {"portfolio_id": "1111"}) as span:
        result = await tool(...)
        record_tool_result(span, output=result)

Spans are always real objects (no-ops when tracing is off), so callers never check for None. An exception that
escapes a span marks it as failed automatically; use record_error for failures that are handled.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from openinference.instrumentation import get_input_attributes, get_output_attributes
from openinference.semconv.trace import SpanAttributes
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from app.observability.conventions import Attr, SpanKind, SpanName
from app.observability.setup import get_config, get_tracer

_PRIMITIVES = (str, bool, int, float)


def set_attributes(span: Span, attributes: Mapping[str, Any]) -> None:
    """Set attributes, skipping None; lists of primitives stay lists, anything else is stored as JSON text."""
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, _PRIMITIVES):
            span.set_attribute(key, value)
        elif isinstance(value, (list, tuple)) and all(isinstance(item, _PRIMITIVES) for item in value):
            span.set_attribute(key, list(value))
        else:
            span.set_attribute(key, json.dumps(value, default=str))


def set_current_attributes(attributes: Mapping[str, Any]) -> None:
    """Add attributes to the span that is current right now (for code that does not hold the span)."""
    set_attributes(trace.get_current_span(), attributes)


def capture_content() -> bool:
    return get_config().capture_content


@contextmanager
def start_span(
    name: str,
    *,
    kind: str = SpanKind.CHAIN,
    attributes: Mapping[str, Any] | None = None,
    input_value: Any = None,
) -> Iterator[Span]:
    """A timed span, nested under the current one. input_value is recorded only when content capture is on."""
    with get_tracer().start_as_current_span(name) as span:
        set_attributes(span, {SpanAttributes.OPENINFERENCE_SPAN_KIND: kind, **(attributes or {})})
        if input_value is not None and capture_content():
            set_attributes(span, get_input_attributes(input_value))
        yield span


def start_agent_span(agent: str, *, input_value: Any = None, attributes: Mapping[str, Any] | None = None):
    return start_span(
        SpanName.agent(agent),
        kind=SpanKind.AGENT,
        attributes={Attr.AGENT_NAME: agent, **(attributes or {})},
        input_value=input_value,
    )


def start_tool_span(tool: str, arguments: Mapping[str, Any] | None = None, *, attributes: Mapping[str, Any] | None = None):
    """A TOOL span; the arguments are its input (OpenInference tool.name / input.value)."""
    return start_span(
        SpanName.tool(tool),
        kind=SpanKind.TOOL,
        attributes={"tool.name": tool, **(attributes or {})},
        input_value=json.dumps(dict(arguments or {}), default=str),
    )


def record_output(span: Span, output: Any) -> None:
    if output is not None and capture_content():
        set_attributes(span, get_output_attributes(output))


def record_tool_result(
    span: Span,
    *,
    output: Any = None,
    error: str | None = None,
    error_kind: str | None = None,
    attempts: int = 1,
    no_data: bool = False,
) -> None:
    """Outcome of a tool call: output, or a handled error with its kind; attempts > 1 means it was retried."""
    set_attributes(
        span,
        {
            Attr.RETRY_ATTEMPTS: attempts,
            Attr.STATUS: "error" if error else ("no_data" if no_data else "ok"),
            Attr.ERROR_KIND: error_kind,
        },
    )
    if error:
        mark_error(span, error)
    else:
        record_output(span, output)


def record_error(span: Span, exc: BaseException, *, kind: str | None = None) -> None:
    """A handled exception: recorded on the span and the span marked as failed."""
    span.record_exception(exc)
    mark_error(span, f"{type(exc).__name__}: {exc}")
    set_attributes(span, {"error.type": type(exc).__name__, Attr.ERROR_KIND: kind})


def mark_error(span: Span, description: str) -> None:
    span.set_status(Status(StatusCode.ERROR, description[:500]))
