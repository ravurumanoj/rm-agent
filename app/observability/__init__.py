"""Portable LLM observability for Arize AX: tracing, correlated JSON logs, and the attributes metrics come from.

Self-contained: it imports nothing from the host application, so the folder can be copied into any project
(dependencies: opentelemetry-sdk, opentelemetry-exporter-otlp, openinference-instrumentation, arize-otel).

Integration in four steps:

1. Startup
    setup_observability(ObservabilityConfig(service_name="my-app", project_name="my-app",
                                            backend="arize", space_id=SPACE_ID, api_key=API_KEY))
    configure_logging(service_name="my-app", log_format="json", loggers=["my_app"],
                      log_file="logs/app.log", audit_log_file="logs/audit.log")

2. Entry point of each query: bind identity, open the root span (always a new trace)
    with query_context(correlation_id=request_id, session_id=chat_id, user_id=user_id):
        with start_trace(input_value=question) as span:
            answer = await handle(question)
            set_attributes(span, {Attr.OUTCOME: "answer"})
            record_output(span, answer)

3. Inside: one span per step, agent, tool call and LLM call (graph_node: a node of the Arize AX agent graph)
    with start_span(SpanName.node("router"), graph_node="router"): ...
    with start_agent_span("portfolio"): ...
    with start_tool_span("get_holdings", args) as span: record_tool_result(span, output=result)
    with start_llm_span(caller="router.classify", model=m, provider="openai", messages=msgs) as span:
        record_llm_response(span, output=text, usage=normalize_token_usage(raw_usage), messages=msgs)
    A handled failure: record_failure(span, exc, where="router fell back to the default route")

4. Shutdown
    shutdown_observability()

Arize AX derives latency percentiles, error rates, token and cost metrics from these spans; quality signals and
agent decisions are the Attr.* keys, available for custom charts, monitors and filters.
"""

from app.observability.context import (
    QueryContext,
    bind_query_context,
    current_query_context,
    get_correlation_id,
    new_correlation_id,
    query_context,
    reset_query_context,
)
from app.observability.conventions import Attr, LogField, LogStream, SpanKind, SpanName
from app.observability.llm import (
    estimate_token_usage,
    normalize_token_usage,
    record_llm_response,
    start_llm_span,
)
from app.observability.logs import configure_logging, correlation_fields, get_audit_logger
from app.observability.setup import (
    BACKEND_ARIZE,
    BACKEND_OTLP,
    ObservabilityConfig,
    get_config,
    get_tracer,
    setup_observability,
    shutdown_observability,
)
from app.observability.spans import (
    mark_error,
    record_current_failure,
    record_error,
    record_failure,
    record_output,
    record_retry,
    record_tool_result,
    set_attributes,
    set_current_attributes,
    start_agent_span,
    start_span,
    start_tool_span,
    start_trace,
)

__all__ = [
    "Attr",
    "BACKEND_ARIZE",
    "BACKEND_OTLP",
    "LogField",
    "LogStream",
    "ObservabilityConfig",
    "QueryContext",
    "SpanKind",
    "SpanName",
    "bind_query_context",
    "configure_logging",
    "correlation_fields",
    "current_query_context",
    "estimate_token_usage",
    "get_audit_logger",
    "get_config",
    "get_correlation_id",
    "get_tracer",
    "mark_error",
    "new_correlation_id",
    "normalize_token_usage",
    "query_context",
    "record_current_failure",
    "record_error",
    "record_failure",
    "record_llm_response",
    "record_output",
    "record_retry",
    "record_tool_result",
    "reset_query_context",
    "set_attributes",
    "set_current_attributes",
    "setup_observability",
    "shutdown_observability",
    "start_agent_span",
    "start_llm_span",
    "start_span",
    "start_tool_span",
    "start_trace",
]
