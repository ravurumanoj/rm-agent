"""Tracer setup for Arize AX (or any OTLP collector such as a local Phoenix), done once per process.

    config = ObservabilityConfig(service_name="rm-agent", project_name="rm-agent",
                                 backend="arize", space_id="...", api_key="...")
    setup_observability(config)      # at startup; never raises, returns False when tracing stays off
    shutdown_observability()         # at shutdown; flushes buffered spans

Without setup (or when disabled) every span helper still works and simply records nothing.
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from openinference.semconv.resource import ResourceAttributes
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor

from app.observability.context import NO_CORRELATION_ID, current_query_context
from app.observability.conventions import Attr

log = logging.getLogger(__name__)

BACKEND_ARIZE = "arize"  # Arize AX through arize.otel.register (space id + API key)
BACKEND_OTLP = "otlp"  # any OTLP/HTTP collector, e.g. a local Phoenix at http://127.0.0.1:6006/v1/traces
TRACER_NAME = "app.observability"


@dataclass(frozen=True)
class ObservabilityConfig:
    enabled: bool = True
    service_name: str = "llm-app"
    service_version: str = ""
    project_name: str = "default"
    backend: str = BACKEND_ARIZE
    endpoint: str = ""  # arize: optional regional/private endpoint; otlp: collector URL (required)
    space_id: str = ""
    api_key: str = ""
    headers: dict[str, str] = field(default_factory=dict)  # extra OTLP headers (otlp backend)
    certificate_file: str = ""  # CA bundle for TLS-intercepting proxies (otlp backend)
    capture_content: bool = True  # prompts, responses and tool payloads on spans
    llm_provider_label: str = ""  # overrides llm.provider so it matches the Arize AX cost config
    tiktoken_estimates: bool = False  # estimate missing token counts with tiktoken instead of ~4 chars/token
    instrument_langchain: bool = False  # LangChain auto-instrumentation; off when the app writes its own spans


@dataclass
class _State:
    config: ObservabilityConfig = field(default_factory=lambda: ObservabilityConfig(enabled=False))
    provider: Any = None


_state = _State()


def get_config() -> ObservabilityConfig:
    return _state.config


def get_tracer() -> trace.Tracer:
    """A no-op tracer until setup succeeds, so instrumented code never needs an "is tracing on" check."""
    return trace.get_tracer(TRACER_NAME)


class QueryContextSpanProcessor(SpanProcessor):
    """Stamps correlation id, session id and user id on every span at start, including library spans."""

    def on_start(self, span: Span, parent_context: Any = None) -> None:
        context = current_query_context()
        if context.correlation_id != NO_CORRELATION_ID:
            span.set_attribute(Attr.CORRELATION_ID, context.correlation_id)
        if context.session_id:
            span.set_attribute(Attr.SESSION_ID, context.session_id)
        if context.user_id:
            span.set_attribute(Attr.USER_ID, context.user_id)

    def on_end(self, span: ReadableSpan) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def setup_observability(config: ObservabilityConfig) -> bool:
    """Create and register the tracer provider. Idempotent; never raises."""
    if _state.provider is not None:
        return True
    _state.config = config
    if not config.enabled:
        log.info("[OBS] disabled")
        return False
    try:
        provider = _arize_provider(config) if config.backend == BACKEND_ARIZE else _otlp_provider(config)
    except Exception:
        log.exception("[OBS] setup_failed backend=%s; continuing untraced", config.backend)
        return False
    if provider is None:
        return False

    provider.add_span_processor(QueryContextSpanProcessor())
    _state.provider = provider
    if config.instrument_langchain:
        _instrument_langchain(provider)
    log.info(
        "[OBS] ready backend=%s project=%s endpoint=%s capture_content=%s langchain_instrumented=%s",
        config.backend, config.project_name, config.endpoint or "default", config.capture_content,
        config.instrument_langchain,
    )
    return True


def shutdown_observability(timeout_millis: int = 5000) -> None:
    """Flush buffered spans (the batch processor holds them in memory) and stop the provider."""
    provider = _state.provider
    if provider is None:
        return
    try:
        provider.force_flush(timeout_millis=timeout_millis)
        provider.shutdown()
    except Exception:
        log.warning("[OBS] flush_failed; buffered spans may be lost", exc_info=True)
    _state.provider = None


def _arize_provider(config: ObservabilityConfig) -> Any:
    from arize.otel import Transport, register

    if not (config.space_id and config.api_key):
        log.warning("[OBS] not_started backend=arize reason=missing_space_id_or_api_key")
        return None
    options: dict[str, Any] = {
        "space_id": config.space_id,
        "api_key": config.api_key,
        "project_name": config.project_name,
        "set_global_tracer_provider": True,
        "batch": True,
    }
    if config.endpoint:
        options.update(endpoint=config.endpoint, transport=Transport.HTTP)
    return register(**options)


def _otlp_provider(config: ObservabilityConfig) -> Any:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    if not config.endpoint:
        log.warning("[OBS] not_started backend=otlp reason=missing_endpoint")
        return None
    if _is_local(config.endpoint) and not _reachable(config.endpoint):
        log.warning("[OBS] not_started backend=otlp reason=local_collector_unreachable endpoint=%s", config.endpoint)
        return None

    resource = Resource.create(
        {
            "service.name": config.service_name,
            "service.version": config.service_version,
            ResourceAttributes.PROJECT_NAME: config.project_name,
        }
    )
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=config.endpoint, headers=config.headers or None, certificate_file=config.certificate_file or None
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return provider


def _instrument_langchain(provider: Any) -> None:
    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor

        LangChainInstrumentor().instrument(tracer_provider=provider)
    except Exception:
        log.warning("[OBS] langchain_instrumentation_unavailable", exc_info=True)


def _is_local(endpoint: str) -> bool:
    return (urlparse(endpoint).hostname or "") in {"127.0.0.1", "localhost"}


def _reachable(endpoint: str, timeout: float = 0.25) -> bool:
    parsed = urlparse(endpoint)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout):
            return True
    except OSError:
        return False
