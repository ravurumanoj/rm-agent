"""Per-query identity (correlation id, session, user) carried through async code.

Bind it once at the entry point; every span, log line and metric attribute created inside inherits it:

    with query_context(correlation_id=request_id, session_id=chat_id, user_id=user):
        ...  # spans get app.correlation_id / session.id / user.id, logs get correlation_id

Child tasks (asyncio, LangGraph branches) inherit it automatically through contextvars.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from typing import Iterator

NO_CORRELATION_ID = "-"


@dataclass(frozen=True)
class QueryContext:
    correlation_id: str = NO_CORRELATION_ID
    session_id: str = ""
    user_id: str = ""


_current: ContextVar[QueryContext] = ContextVar("observability_query_context", default=QueryContext())


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def current_query_context() -> QueryContext:
    return _current.get()


def get_correlation_id() -> str:
    return _current.get().correlation_id


def bind_query_context(
    *, correlation_id: str | None = None, session_id: str | None = None, user_id: str | None = None
) -> Token:
    """Set identity fields (unset ones keep their current value); returns a token for reset_query_context."""
    current = _current.get()
    updated = replace(
        current,
        correlation_id=(correlation_id or "").strip() or current.correlation_id,
        session_id=(session_id or "").strip() or current.session_id,
        user_id=(user_id or "").strip() or current.user_id,
    )
    return _current.set(updated)


def reset_query_context(token: Token) -> None:
    _current.reset(token)


@contextmanager
def query_context(
    *, correlation_id: str | None = None, session_id: str | None = None, user_id: str | None = None
) -> Iterator[QueryContext]:
    """Bind identity for the block; a missing correlation id is generated so every query has one."""
    if not correlation_id and get_correlation_id() == NO_CORRELATION_ID:
        correlation_id = new_correlation_id()
    token = bind_query_context(correlation_id=correlation_id, session_id=session_id, user_id=user_id)
    try:
        yield _current.get()
    finally:
        reset_query_context(token)
