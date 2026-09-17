import json
import logging
import sys
from contextvars import ContextVar, Token
from pathlib import Path

from app.config import settings


_correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="-")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = get_correlation_id()
        return True


class ExtraFieldsFormatter(logging.Formatter):
    """Append non-standard LogRecord fields as compact JSON when present."""

    _reserved_fields = {
        "args",
        "asctime",
        "created",
        "correlation_id",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in self._reserved_fields and not key.startswith("_")
        }
        if not extras:
            return message
        return f"{message} extras={json.dumps(extras, default=str, sort_keys=True)}"


def set_correlation_id(correlation_id: str) -> Token:
    """Set request correlation id in context and return reset token."""
    cid = (correlation_id or "").strip() or "-"
    return _correlation_id_ctx.set(cid)


def reset_correlation_id(token: Token) -> None:
    """Reset request correlation id context to prior value."""
    _correlation_id_ctx.reset(token)


def get_correlation_id() -> str:
    """Return currently bound correlation id for this execution context."""
    return _correlation_id_ctx.get()

def setup_logger(name: str = "rm_agent", level: int = logging.INFO) -> logging.Logger:
    """Setup app logger with common metadata in each line."""
    app_logger = logging.getLogger(name)
    app_logger.setLevel(level)

    if app_logger.handlers:
        return app_logger

    formatter = ExtraFieldsFormatter(
        "%(asctime)s %(levelname)s correlation_id=%(correlation_id)s %(name)s %(filename)s:%(lineno)d %(funcName)s %(message)s"
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    console_handler.addFilter(CorrelationIdFilter())
    app_logger.addHandler(console_handler)

    if settings.ENABLE_FILE_LOGGING and settings.LOG_FILE.strip():
        log_path = Path(settings.LOG_FILE)
        if not log_path.is_absolute():
            log_path = Path(__file__).resolve().parents[2] / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        file_handler.addFilter(CorrelationIdFilter())
        app_logger.addHandler(file_handler)

    app_logger.propagate = False

    return app_logger


logger = setup_logger()


def configure_logging(level: int = logging.INFO) -> None:
    """Configure app logger level at startup."""
    logger.setLevel(level)
    for handler in logger.handlers:
        handler.setLevel(level)
