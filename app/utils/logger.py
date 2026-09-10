import logging
import sys
from contextvars import ContextVar, Token


_correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="-")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = get_correlation_id()
        return True


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

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s correlation_id=%(correlation_id)s %(name)s %(filename)s:%(lineno)d %(funcName)s %(message)s"
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    console_handler.addFilter(CorrelationIdFilter())
    app_logger.addHandler(console_handler)
    app_logger.propagate = False

    return app_logger


logger = setup_logger()


def configure_logging(level: int = logging.INFO) -> None:
    """Configure app logger level at startup."""
    logger.setLevel(level)
    for handler in logger.handlers:
        handler.setLevel(level)
