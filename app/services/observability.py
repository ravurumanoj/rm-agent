from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional

from langchain_core.messages import BaseMessage

from app.config import settings
from app.utils.logger import logger

_observability_initialized = False
_tracer = None


def configure_observability() -> None:
    """Initialize Phoenix-compatible tracing once per process."""
    global _observability_initialized
    global _tracer

    if _observability_initialized:
        return

    if not settings.PHOENIX_ENABLED:
        logger.info("[OBS] phoenix_disabled")
        _observability_initialized = True
        return

    try:
        from opentelemetry import trace  # type: ignore[reportMissingImports]
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[reportMissingImports]
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource  # type: ignore[reportMissingImports]
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore[reportMissingImports]
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore[reportMissingImports]
    except Exception as exc:
        logger.warning("[OBS] telemetry_dependencies_missing error=%s", exc)
        _observability_initialized = True
        return

    resource = Resource.create(
        {
            "service.name": settings.APP_NAME,
            "service.version": settings.VERSION,
            "phoenix.project_name": settings.PHOENIX_PROJECT_NAME,
        }
    )
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=settings.PHOENIX_EFFECTIVE_OTLP_ENDPOINT,
        headers=settings.PHOENIX_EFFECTIVE_OTLP_HEADERS,
    )
    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("rm_agent.observability")

    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor  # type: ignore[reportMissingImports]

        LangChainInstrumentor().instrument()
        logger.info("[OBS] openinference_langchain_instrumented")
    except Exception as exc:
        logger.warning("[OBS] openinference_instrumentation_unavailable error=%s", exc)

    logger.info(
        "[OBS] phoenix_enabled mode=%s endpoint=%s project=%s headers=%s",
        "local" if settings.PHOENIX_LOCAL_MODE else "deployed",
        settings.PHOENIX_EFFECTIVE_OTLP_ENDPOINT,
        settings.PHOENIX_PROJECT_NAME,
        sorted(settings.PHOENIX_EFFECTIVE_OTLP_HEADERS.keys()),
    )
    _observability_initialized = True


def _safe_set_attributes(span: Any, attributes: dict[str, Any]) -> None:
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            span.set_attribute(key, value)
        else:
            span.set_attribute(key, str(value))


def _message_preview(messages: list[BaseMessage], max_len: int = 400) -> str:
    if not messages:
        return ""
    first = messages[-1]
    content = getattr(first, "content", "")
    text = content if isinstance(content, str) else str(content)
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


@contextmanager
def llm_span(
    span_name: str,
    *,
    model: str,
    provider: str,
    messages: list[BaseMessage],
    metadata: Optional[dict[str, Any]] = None,
) -> Iterator[Optional[Any]]:
    """Create one LLM invocation span if observability is enabled."""
    if _tracer is None:
        yield None
        return

    with _tracer.start_as_current_span(span_name) as span:
        attrs = {
            "llm.model": model,
            "llm.provider": provider,
            "llm.message_count": len(messages),
        }
        if settings.PHOENIX_CAPTURE_MESSAGE_CONTENT:
            attrs["llm.input.preview"] = _message_preview(messages)
        if metadata:
            for key, value in metadata.items():
                attrs[f"app.{key}"] = value
        _safe_set_attributes(span, attrs)
        yield span
