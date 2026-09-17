from __future__ import annotations

from contextlib import contextmanager
from urllib.parse import urlparse
import socket
from typing import Any

from app.config import settings
from app.services.tracing import llm_span, operation_span, record_exception, record_llm_output, record_llm_result
from app.utils.logger import logger

_observability_initialized = False
_tracer = None
_provider = None


class _LoggingOTLPSpanExporter:
    """Wrap OTLP exporter so export failures are surfaced in app logs."""

    def __init__(self, exporter: Any) -> None:
        self._exporter = exporter

    def export(self, spans: Any) -> Any:
        try:
            result = self._exporter.export(spans)
            result_code = getattr(result, "name", None) or getattr(result, "value", result)
            if str(result_code).upper() not in {"SUCCESS", "0"}:
                logger.warning(
                    "[OBS] span_export_failed result=%s span_count=%s endpoint=%s",
                    result_code,
                    len(spans) if spans is not None else 0,
                    settings.PHOENIX_EFFECTIVE_OTLP_ENDPOINT,
                )
            return result
        except Exception as exc:
            logger.exception(
                "[OBS] span_export_exception endpoint=%s error=%s",
                settings.PHOENIX_EFFECTIVE_OTLP_ENDPOINT,
                exc,
            )
            raise

    def shutdown(self) -> Any:
        return self._exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> Any:
        force_flush = getattr(self._exporter, "force_flush", None)
        if callable(force_flush):
            return force_flush(timeout_millis=timeout_millis)
        return True


def _is_local_phoenix_endpoint_reachable(endpoint: str) -> bool:
    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").strip().lower()
    port = parsed.port
    if not host or port is None:
        return False
    if host not in {"127.0.0.1", "localhost"}:
        return True

    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def configure_observability() -> None:
    """Initialize OpenTelemetry tracing once per process.

    Supports two backends behind the same ``PHOENIX_*`` settings:

    * ``PHOENIX_LOCAL_MODE=True``  -> local Phoenix collector (no auth headers).
    * ``PHOENIX_LOCAL_MODE=False`` -> enterprise Arize collector, authenticated
      with ``PHOENIX_SPACE_ID`` / ``PHOENIX_API_KEY`` headers.

    The function is idempotent and never raises: if telemetry dependencies are
    missing or tracing is disabled, the process continues untraced and
    ``_tracer`` stays ``None`` so all span helpers become no-ops.
    """
    global _observability_initialized
    global _tracer
    global _provider

    if _observability_initialized:
        return

    if not settings.PHOENIX_ENABLED:
        logger.info("[OBS] phoenix_disabled")
        _observability_initialized = True
        return

    if settings.PHOENIX_LOCAL_MODE and not _is_local_phoenix_endpoint_reachable(settings.PHOENIX_EFFECTIVE_OTLP_ENDPOINT):
        logger.warning(
            "[OBS] phoenix_local_collector_unreachable endpoint=%s tracing_disabled=true",
            settings.PHOENIX_EFFECTIVE_OTLP_ENDPOINT,
        )
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

    # Project routing differs per backend: Phoenix groups traces by
    # `phoenix.project_name`, Arize groups them by `model_id` / `model_version`.
    resource_attrs: dict[str, Any] = {
        "service.name": settings.APP_NAME,
        "service.version": settings.VERSION,
    }
    if settings.PHOENIX_LOCAL_MODE:
        resource_attrs["phoenix.project_name"] = settings.PHOENIX_PROJECT_NAME
    else:
        resource_attrs["model_id"] = settings.PHOENIX_PROJECT_NAME
        resource_attrs["model_version"] = settings.VERSION

    resource = Resource.create(resource_attrs)
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=settings.PHOENIX_EFFECTIVE_OTLP_ENDPOINT,
        headers=settings.PHOENIX_EFFECTIVE_OTLP_HEADERS,
    )
    processor = BatchSpanProcessor(_LoggingOTLPSpanExporter(exporter))
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    _provider = provider
    _tracer = trace.get_tracer("rm_agent.observability")

    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor  # type: ignore[reportMissingImports]

        # Bind explicitly to this provider instead of relying on global state.
        LangChainInstrumentor().instrument(tracer_provider=provider)
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


def shutdown_observability() -> None:
    """Flush buffered spans and shut the tracer provider down.

    ``BatchSpanProcessor`` queues spans in memory, so a remote collector loses
    the final batch when the process exits abruptly. Call this after the
    ``yield`` in the FastAPI lifespan handler. Safe to call when tracing was
    never initialized.
    """
    if _provider is None:
        return
    try:
        _provider.force_flush(timeout_millis=5000)
        _provider.shutdown()
        logger.info("[OBS] tracer_provider_flushed")
    except Exception as exc:
        logger.warning("[OBS] flush_failed error=%s", exc)


