"""Span helpers for the query root, workflow steps, agents and tool calls (LLM calls are in llm.py).

    with start_trace(input_value=question) as root:          # one query: always the root of a new trace
        with start_span(SpanName.node("orchestrator"), graph_node="orchestrator") as span:
            ...
            set_attributes(span, {Attr.DECISION_INTENT: "data_request"})

    with start_tool_span("get_portfolio_summary", {"portfolio_id": "1111"}) as span:
        result = await tool(...)
        record_tool_result(span, output=result)

Spans are always real objects (no-ops when tracing is off), so callers never check for None. An exception or a
cancellation (e.g. a timeout) that escapes a span marks it as failed automatically; use record_failure for failures
that are handled, so the span says where and why it failed.

Spans opened with graph_node are nodes of the agent graph Arize AX draws (OpenInference graph.node.id / parent_id):
the parent is the nearest enclosing graph node, and app.graph.step numbers the nodes of a query in start order.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from typing import Any, Iterator, Mapping

from openinference.instrumentation import get_input_attributes, get_output_attributes
from openinference.semconv.trace import SpanAttributes
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Span, Status, StatusCode

from app.observability.conventions import Attr, SpanKind, SpanName
from app.observability.setup import get_config, get_tracer

log = logging.getLogger(__name__)

_PRIMITIVES = (str, bool, int, float)
_MAX_HTTP_BODY_CHARS = 2000


@dataclass(frozen=True)
class _GraphPosition:
    """The nearest enclosing graph node and the step counter of the current query."""

    node: str = ""
    steps: Iterator[int] | None = None


_graph_position: ContextVar[_GraphPosition] = ContextVar("observability_graph_position", default=_GraphPosition())


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
    graph_node: str | None = None,
    new_trace: bool = False,
) -> Iterator[Span]:
    """A timed span, nested under the current one; new_trace makes it the root of a new trace instead.

    input_value is recorded only when content capture is on. graph_node makes the span a node of the agent graph.
    """
    position = _GraphPosition(steps=itertools.count()) if new_trace else _graph_position.get()
    # Failures are recorded below rather than by OpenTelemetry, so they read well and cancellations count too.
    with get_tracer().start_as_current_span(
        name, context=Context() if new_trace else None, record_exception=False, set_status_on_exception=False
    ) as span:
        set_attributes(span, {SpanAttributes.OPENINFERENCE_SPAN_KIND: kind, **(attributes or {})})
        if input_value is not None and capture_content():
            set_attributes(span, get_input_attributes(input_value))
        if graph_node:
            set_attributes(
                span,
                {
                    Attr.GRAPH_NODE_ID: graph_node,
                    Attr.GRAPH_NODE_NAME: graph_node,
                    Attr.GRAPH_NODE_PARENT_ID: position.node or None,
                    Attr.GRAPH_STEP: next(position.steps) if position.steps is not None else None,
                },
            )
            position = replace(position, node=graph_node)
        token = _graph_position.set(position)
        try:
            yield span
        except (Exception, asyncio.CancelledError) as exc:
            if not _has_failed(span):  # keep a failure the code already explained with record_failure
                record_failure(span, exc)
            raise
        finally:
            _reset(_graph_position, token)


def start_trace(name: str = SpanName.QUERY, *, input_value: Any = None, attributes: Mapping[str, Any] | None = None):
    """The root span of one query, always the root of a new trace even when a span is current (e.g. the HTTP request
    span a background task inherits), so the trace's latency, input and output are the query's own.

    It is also the root node of the agent graph. Bind query_context first, so it carries session.id and user.id.
    """
    return start_span(
        name, kind=SpanKind.CHAIN, attributes=attributes, input_value=input_value, graph_node=name, new_trace=True
    )


def start_agent_span(
    agent: str,
    *,
    input_value: Any = None,
    attributes: Mapping[str, Any] | None = None,
    graph_node: str | None = None,
):
    """An AGENT span (agent.name), a node of the agent graph named graph_node (default: the agent)."""
    return start_span(
        SpanName.agent(agent),
        kind=SpanKind.AGENT,
        attributes={Attr.AGENT_NAME: agent, **(attributes or {})},
        input_value=input_value,
        graph_node=graph_node or agent,
    )


def start_tool_span(tool: str, arguments: Mapping[str, Any] | None = None, *, attributes: Mapping[str, Any] | None = None):
    """A TOOL span, a node of the agent graph under the calling agent; the arguments are its input
    (OpenInference tool.name / input.value)."""
    return start_span(
        SpanName.tool(tool),
        kind=SpanKind.TOOL,
        attributes={"tool.name": tool, **(attributes or {})},
        input_value=json.dumps(dict(arguments or {}), default=str),
        graph_node=tool,
    )


def record_output(span: Span, output: Any) -> None:
    if output is None or not capture_content():
        return
    try:
        set_attributes(span, get_output_attributes(output))
    except Exception:  # tracing must never fail the work it describes
        log.warning("[OBS] output_not_recorded span=%s", getattr(span, "name", "-"), exc_info=True)


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
        record_failure(span, error, kind=error_kind)
    else:
        record_output(span, output)


def record_failure(
    span: Span,
    error: BaseException | str,
    *,
    where: str = "",
    kind: str | None = None,
    attributes: Mapping[str, Any] | None = None,
) -> None:
    """Mark the span failed and say where and why, so the failure reads on its own in Arize AX.

    Records the ERROR status "<where>: <what>", an exception event (with the stack trace when an exception is given),
    error.type, app.error.kind / app.error.where, the HTTP status and body an exception carries, and the message as
    the span's output when it has none. Keep client data out of `where`: status and events are recorded even when
    content capture is off. Never raises: it runs inside error handlers, where it must not replace the failure.
    """
    try:
        exc = error if isinstance(error, BaseException) else None
        if kind is None and isinstance(exc, asyncio.CancelledError):
            kind = "cancelled"
        message = f"{where}: {_describe(error)}" if where else _describe(error)
        span.set_status(Status(StatusCode.ERROR, message[:500]))
        if exc is not None:
            span.record_exception(exc, attributes={"exception.message": message})
        else:
            span.add_event("exception", {"exception.type": kind or "error", "exception.message": message})
        http_status, http_body = _http_details(exc)
        set_attributes(
            span,
            {
                Attr.ERROR_TYPE: type(exc).__name__ if exc is not None else (kind or "error"),
                Attr.ERROR_KIND: kind,
                Attr.ERROR_WHERE: where or None,
                Attr.ERROR_HTTP_STATUS: http_status,
                Attr.ERROR_HTTP_BODY: http_body if capture_content() else None,
                **(attributes or {}),
            },
        )
        if not _has_output(span):
            set_attributes(span, get_output_attributes(f"ERROR: {message}"))
    except Exception:
        log.warning("[OBS] failure_not_recorded span=%s", getattr(span, "name", "-"), exc_info=True)


def record_current_failure(error: BaseException | str, *, where: str = "", kind: str | None = None) -> None:
    """record_failure on the span that is current right now (for code that does not hold the span)."""
    record_failure(trace.get_current_span(), error, where=where, kind=kind)


def record_retry(span: Span, attempt: int, error: BaseException, delay: float) -> None:
    """One failed attempt that is retried, as a span event: which attempt, why it failed, the wait before the next."""
    span.add_event(
        "retry",
        {
            "app.retry.attempt": attempt,
            "app.retry.delay_seconds": round(delay, 2),
            Attr.ERROR_TYPE: type(error).__name__,
            "exception.message": _describe(error)[:500],
        },
    )


def record_error(span: Span, exc: BaseException, *, kind: str | None = None, where: str = "") -> None:
    """A handled exception (record_failure with an exception)."""
    record_failure(span, exc, where=where, kind=kind)


def mark_error(span: Span, description: str) -> None:
    """A handled failure without an exception (record_failure with a message)."""
    record_failure(span, description)


def _describe(error: BaseException | str) -> str:
    """"<Type>: <message>", with a readable fallback when there is no message (e.g. TimeoutError())."""
    if isinstance(error, str):
        return error.strip() or "failed without details"
    if isinstance(error, asyncio.CancelledError):
        return "CancelledError: stopped before it finished (timeout, client disconnect or shutdown)"
    text = str(error).strip() or ("timed out" if isinstance(error, TimeoutError) else "no error message")
    return f"{type(error).__name__}: {text}"


def _http_details(exc: BaseException | None) -> tuple[int | None, str | None]:
    """HTTP status and response body an HTTP/SDK client error carries (httpx, openai and unique_sdk spellings)."""
    if exc is None:
        return None, None
    response = getattr(exc, "response", None)
    candidates = (getattr(exc, "status_code", None), getattr(exc, "http_status", None), getattr(response, "status_code", None))
    status = next((value for value in candidates if isinstance(value, int)), None)
    body = getattr(exc, "http_body", None) or getattr(exc, "body", None)
    if body is None and response is not None:
        try:
            body = response.text
        except Exception:  # e.g. a streamed response that was never read
            body = None
    if body is not None and not isinstance(body, str):
        body = json.dumps(body, default=str)
    return status, (body[:_MAX_HTTP_BODY_CHARS] if body else None)


def _has_failed(span: Span) -> bool:
    status = getattr(span, "status", None)
    return status is not None and status.status_code is StatusCode.ERROR


def _has_output(span: Span) -> bool:
    return bool((getattr(span, "attributes", None) or {}).get(SpanAttributes.OUTPUT_VALUE))


def _reset(var: ContextVar, token: Token) -> None:
    try:
        var.reset(token)
    except ValueError:  # closed from another context (e.g. an abandoned stream generator): nothing to restore
        pass
