"""Structured JSON logging correlated with traces, in two streams: operational and audit.

    configure_logging(service_name="rm-agent", level="INFO", log_format="json",
                      log_file="logs/app.log", audit_log_file="logs/audit.log")
    log = logging.getLogger("rm_agent")             # operational stream
    audit = get_audit_logger()                       # compliance audit stream
    audit.info("query_completed", extra={"outcome": "answer"})

Every line carries correlation_id, session_id, trace_id and span_id, so a log line opens the matching Arize AX
trace. Each line also carries log_stream ("operational" or "audit"): ship both with Filebeat/Logstash and route
by that field to separate ELK indices, each with its own index-lifecycle retention and access roles.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from opentelemetry import trace

from app.observability.context import current_query_context
from app.observability.conventions import LogField, LogStream

AUDIT_LOGGER_NAME = "audit"

# LogRecord attributes that are not user-supplied "extra" fields
_STANDARD_RECORD_FIELDS = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message", "asctime", "taskName", LogField.STREAM, "service",
}


_CORRELATION_FIELDS = {
    LogField.CORRELATION_ID, LogField.SESSION_ID, LogField.USER_ID, LogField.TRACE_ID, LogField.SPAN_ID,
}


def correlation_fields() -> dict[str, str]:
    """Correlation id, session, user and the current trace/span ids (hex, as shown in Arize AX)."""
    context = current_query_context()
    fields = {LogField.CORRELATION_ID: context.correlation_id}
    if context.session_id:
        fields[LogField.SESSION_ID] = context.session_id
    if context.user_id:
        fields[LogField.USER_ID] = context.user_id
    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        fields[LogField.TRACE_ID] = format(span_context.trace_id, "032x")
        fields[LogField.SPAN_ID] = format(span_context.span_id, "016x")
    return fields


class CorrelationFilter(logging.Filter):
    """Adds the correlation fields and the stream name to each record."""

    def __init__(self, stream: str) -> None:
        super().__init__()
        self.stream = stream

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in correlation_fields().items():
            setattr(record, key, value)
        setattr(record, LogField.STREAM, self.stream)
        return True


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line; user "extra" fields are kept as top-level keys."""

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            LogField.TIMESTAMP: datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            LogField.LEVEL: record.levelname,
            LogField.LOGGER: record.name,
            LogField.MESSAGE: record.getMessage(),
            LogField.SERVICE: self.service_name,
            LogField.STREAM: getattr(record, LogField.STREAM, LogStream.OPERATIONAL),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_") and key not in entry:
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


class TextLogFormatter(logging.Formatter):
    """Human-readable lines for local development, with the same correlation fields and any "extra" fields."""

    def __init__(self) -> None:
        super().__init__(
            "%(asctime)s %(levelname)s correlation_id=%(correlation_id)s %(name)s "
            "%(filename)s:%(lineno)d %(funcName)s %(message)s"
        )

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_FIELDS and key not in _CORRELATION_FIELDS and not key.startswith("_")
        }
        return f"{line} extras={json.dumps(extras, default=str, sort_keys=True)}" if extras else line


def configure_logging(
    *,
    service_name: str,
    level: str | int = "INFO",
    log_format: str = "json",
    log_file: str | None = None,
    audit_log_file: str | None = None,
    loggers: Iterable[str] = (),
) -> None:
    """Attach correlated handlers to the given operational loggers and to the audit logger. Idempotent."""
    level = logging.getLevelName(level.upper()) if isinstance(level, str) else level
    formatter: logging.Formatter = JsonLogFormatter(service_name) if log_format == "json" else TextLogFormatter()

    operational_handlers = _handlers(formatter, LogStream.OPERATIONAL, log_file)
    for name in loggers:
        _install(logging.getLogger(name), operational_handlers, level)

    _install(logging.getLogger(AUDIT_LOGGER_NAME), _handlers(formatter, LogStream.AUDIT, audit_log_file), logging.INFO)


def get_audit_logger() -> logging.Logger:
    """The compliance audit stream: separate handlers, never mixed into operational logs."""
    return logging.getLogger(AUDIT_LOGGER_NAME)


def _handlers(formatter: logging.Formatter, stream: str, log_file: str | None) -> list[logging.Handler]:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(CorrelationFilter(stream))
    return handlers


def _install(target: logging.Logger, handlers: list[logging.Handler], level: int) -> None:
    for handler in list(target.handlers):
        target.removeHandler(handler)
        handler.close()
    for handler in handlers:
        handler.setLevel(level)
        target.addHandler(handler)
    target.setLevel(level)
    target.propagate = False
