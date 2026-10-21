"""The app's loggers, configured through app.observability.logs (correlated JSON or text, two streams).

Kept as the import point the codebase already uses: ``from app.utils.logger import logger, preview``.
"""

import logging
from contextvars import Token
from pathlib import Path

from app.components.text_preview import configure_preview, preview
from app.config import settings
from app.observability import bind_query_context, get_correlation_id, reset_query_context
from app.observability import configure_logging as configure_observability_logging

__all__ = ["logger", "preview", "configure_logging", "get_correlation_id", "set_correlation_id", "reset_correlation_id"]

APP_LOGGER_NAME = "rm_agent"
# app.components uses stdlib loggers (it must not import app.*); they share the app handlers.
_COMPONENTS_LOGGER_NAME = "app.components"
_OBSERVABILITY_LOGGER_NAME = "app.observability"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def set_correlation_id(correlation_id: str) -> Token:
    """Bind the request's correlation id; returns the token for reset_correlation_id."""
    return bind_query_context(correlation_id=correlation_id)


def reset_correlation_id(token: Token) -> None:
    reset_query_context(token)


def _log_path(path: str) -> str | None:
    if not (settings.ENABLE_FILE_LOGGING and path.strip()):
        return None
    log_path = Path(path)
    return str(log_path if log_path.is_absolute() else _PROJECT_ROOT / log_path)


def configure_logging(level: int | str | None = None) -> None:
    """(Re)configure handlers: LOG_FORMAT json|text, LOG_FILE for operational logs, AUDIT_LOG_FILE for audit."""
    configure_preview(full=settings.LOG_PAYLOAD_FULL, edge_chars=settings.LOG_PAYLOAD_EDGE_CHARS)
    configure_observability_logging(
        service_name=settings.APP_NAME,
        level=level if level is not None else settings.LOG_LEVEL,
        log_format=settings.LOG_FORMAT,
        log_file=_log_path(settings.LOG_FILE),
        audit_log_file=_log_path(settings.AUDIT_LOG_FILE),
        loggers=(APP_LOGGER_NAME, _COMPONENTS_LOGGER_NAME, _OBSERVABILITY_LOGGER_NAME),
    )


configure_logging()
logger = logging.getLogger(APP_LOGGER_NAME)
