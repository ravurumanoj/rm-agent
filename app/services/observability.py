from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urlparse
import socket
from typing import Any

from app.config import settings
from app.services.tracing import llm_span, operation_span, record_exception, record_llm_output, record_llm_result
from app.utils.logger import logger

_observability_initialized = False
_tracer = None
_provider = None


@dataclass(frozen=True)
class _TelemetryDeps:
    trace_api: Any
    exporter_cls: Any
    resource_cls: Any
    provider_cls: Any
    processor_cls: Any


@dataclass(frozen=True)
class _EndpointInfo:
    endpoint: str
    scheme: str
    host: str
    port: int | None
    path: str


class _LoggingOTLPSpanExporter:
    """Wrap OTLP exporter so export failures are surfaced in app logs."""

    def __init__(self, exporter: Any) -> None:
        self._exporter = exporter

    def _exporter_debug_fields(self) -> dict[str, Any]:
        return {
            "endpoint": getattr(self._exporter, "_endpoint", settings.PHOENIX_EFFECTIVE_OTLP_ENDPOINT),
            "timeout": getattr(self._exporter, "_timeout", None),
            "certificate_file": bool(getattr(self._exporter, "_certificate_file", None)),
            "client_key_file": bool(getattr(self._exporter, "_client_key_file", None)),
            "client_certificate_file": bool(getattr(self._exporter, "_client_certificate_file", None)),
            "header_keys": sorted(settings.PHOENIX_EFFECTIVE_OTLP_HEADERS.keys()),
        }

    def export(self, spans: Any) -> Any:
        try:
            result = self._exporter.export(spans)
            result_code = getattr(result, "name", None) or getattr(result, "value", result)
            if str(result_code).upper() not in {"SUCCESS", "0"}:
                debug_fields = self._exporter_debug_fields()
                logger.warning(
                    "[OBS] span_export_failed result=%s span_count=%s endpoint=%s timeout=%s certificate_file=%s client_key_file=%s client_certificate_file=%s header_keys=%s",
                    result_code,
                    len(spans) if spans is not None else 0,
                    debug_fields["endpoint"],
                    debug_fields["timeout"],
                    debug_fields["certificate_file"],
                    debug_fields["client_key_file"],
                    debug_fields["client_certificate_file"],
                    debug_fields["header_keys"],
                )
            return result
        except Exception as exc:
            debug_fields = self._exporter_debug_fields()
            logger.exception(
                "[OBS] span_export_exception endpoint=%s timeout=%s certificate_file=%s client_key_file=%s client_certificate_file=%s header_keys=%s error=%s",
                debug_fields["endpoint"],
                debug_fields["timeout"],
                debug_fields["certificate_file"],
                debug_fields["client_key_file"],
                debug_fields["client_certificate_file"],
                debug_fields["header_keys"],
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


def _parse_endpoint(endpoint: str) -> _EndpointInfo:
    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").strip().lower()
    scheme = (parsed.scheme or "").strip().lower()
    port = parsed.port
    if port is None:
        if scheme == "https":
            port = 443
        elif scheme == "http":
            port = 80

    return _EndpointInfo(
        endpoint=endpoint,
        scheme=scheme or "missing",
        host=host or "missing",
        port=port,
        path=parsed.path or "/",
    )


def _can_connect(endpoint_info: _EndpointInfo, timeout: float) -> tuple[bool, str]:
    if endpoint_info.host == "missing" or endpoint_info.port is None:
        return False, "invalid_endpoint"

    try:
        with socket.create_connection((endpoint_info.host, endpoint_info.port), timeout=timeout):
            return True, ""
    except OSError as exc:
        return False, str(exc)


def _load_telemetry_dependencies() -> _TelemetryDeps | None:
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
        return None

    return _TelemetryDeps(
        trace_api=trace,
        exporter_cls=OTLPSpanExporter,
        resource_cls=Resource,
        provider_cls=TracerProvider,
        processor_cls=BatchSpanProcessor,
    )


def _build_provider(
    deps: _TelemetryDeps,
    endpoint: str,
    headers: dict[str, str],
    resource_attrs: dict[str, Any],
) -> bool:
    global _tracer
    global _provider

    resource = deps.resource_cls.create(resource_attrs)
    provider = deps.provider_cls(resource=resource)
    logger.info(
        "[OBS] exporter_init exporter=%s endpoint=%s header_keys=%s resource_keys=%s",
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
        endpoint,
        sorted(headers.keys()),
        sorted(resource_attrs.keys()),
    )
    try:
        exporter = deps.exporter_cls(
            endpoint=endpoint,
            headers=headers,
        )
    except TypeError as exc:
        logger.exception(
            "[OBS] exporter_init_type_error endpoint=%s header_keys=%s error=%s",
            endpoint,
            sorted(headers.keys()),
            exc,
        )
        return False
    except Exception as exc:
        logger.exception(
            "[OBS] exporter_init_failed endpoint=%s header_keys=%s error=%s",
            endpoint,
            sorted(headers.keys()),
            exc,
        )
        return False

    processor = deps.processor_cls(_LoggingOTLPSpanExporter(exporter))
    provider.add_span_processor(processor)
    deps.trace_api.set_tracer_provider(provider)
    _provider = provider
    _tracer = deps.trace_api.get_tracer("rm_agent.observability")
    logger.info("[OBS] tracer_provider_ready processor=%s", "BatchSpanProcessor")

    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor  # type: ignore[reportMissingImports]

        LangChainInstrumentor().instrument(tracer_provider=provider)
        logger.info("[OBS] openinference_langchain_instrumented")
    except Exception as exc:
        logger.warning("[OBS] openinference_instrumentation_unavailable error=%s", exc)

    return True


def _configure_local_observability(deps: _TelemetryDeps) -> bool:
    endpoint_info = _parse_endpoint(settings.PHOENIX_OTLP_ENDPOINT)
    logger.info(
        "[OBS] config mode=local endpoint=%s scheme=%s host=%s port=%s path=%s header_keys=%s",
        endpoint_info.endpoint,
        endpoint_info.scheme,
        endpoint_info.host,
        endpoint_info.port if endpoint_info.port is not None else "missing",
        endpoint_info.path,
        [],
    )

    reachable, _ = _can_connect(endpoint_info, timeout=0.25)
    if endpoint_info.host in {"127.0.0.1", "localhost"} and not reachable:
        logger.warning(
            "[OBS] phoenix_local_collector_unreachable endpoint=%s tracing_disabled=true",
            endpoint_info.endpoint,
        )
        return False

    resource_attrs: dict[str, Any] = {
        "service.name": settings.APP_NAME,
        "service.version": settings.VERSION,
        "phoenix.project_name": settings.PHOENIX_PROJECT_NAME,
    }
    if not _build_provider(
        deps,
        endpoint_info.endpoint,
        {},
        resource_attrs,
    ):
        return False

    logger.info(
        "[OBS] phoenix_enabled mode=local endpoint=%s project=%s headers=%s",
        endpoint_info.endpoint,
        settings.PHOENIX_PROJECT_NAME,
        [],
    )
    return True


def _configure_deployed_observability(deps: _TelemetryDeps) -> bool:
    endpoint_info = _parse_endpoint(settings.PHOENIX_EFFECTIVE_OTLP_ENDPOINT)
    headers = settings.PHOENIX_EFFECTIVE_OTLP_HEADERS
    logger.info(
        "[OBS] config mode=deployed endpoint=%s scheme=%s host=%s port=%s path=%s header_keys=%s has_space_id=%s has_api_key=%s",
        endpoint_info.endpoint,
        endpoint_info.scheme,
        endpoint_info.host,
        endpoint_info.port if endpoint_info.port is not None else "missing",
        endpoint_info.path,
        sorted(headers.keys()),
        bool(settings.PHOENIX_SPACE_ID.strip()),
        bool(settings.PHOENIX_API_KEY.strip()),
    )

    if endpoint_info.host == "missing" or endpoint_info.port is None:
        logger.warning(
            "[OBS] deployed_endpoint_invalid endpoint=%s scheme=%s host=%s port=%s path=%s",
            endpoint_info.endpoint,
            endpoint_info.scheme,
            endpoint_info.host,
            endpoint_info.port if endpoint_info.port is not None else "missing",
            endpoint_info.path,
        )

    resource_attrs: dict[str, Any] = {
        "service.name": settings.APP_NAME,
        "service.version": settings.VERSION,
        "model_id": settings.PHOENIX_PROJECT_NAME,
        "model_version": settings.VERSION,
    }
    if not _build_provider(
        deps,
        endpoint_info.endpoint,
        headers,
        resource_attrs,
    ):
        return False

    logger.info(
        "[OBS] phoenix_enabled mode=deployed endpoint=%s project=%s headers=%s",
        endpoint_info.endpoint,
        settings.PHOENIX_PROJECT_NAME,
        sorted(headers.keys()),
    )
    return True


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

    telemetry_dependencies = _load_telemetry_dependencies()
    if telemetry_dependencies is None:
        _observability_initialized = True
        return

    if settings.PHOENIX_LOCAL_MODE:
        configured = _configure_local_observability(telemetry_dependencies)
    else:
        configured = _configure_deployed_observability(telemetry_dependencies)

    _observability_initialized = True
    if not configured:
        return


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


