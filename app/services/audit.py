"""Audit logging - persist every tool call / decision for compliance review."""

import time
from typing import Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AuditLog
from app.utils.logger import logger


_AUDIT_DB_COOLDOWN_SECONDS = 30.0
_audit_skip_until = 0.0


def record_event(db: Optional[Session], session_id: str, event: str) -> None:
    """Persist one audit event for a conversation session.

    This function is best-effort and never raises; audit failures should not
    block the main user flow.
    """
    if not settings.AUDIT_ENABLED:
        return

    if db is None:
        return

    global _audit_skip_until
    if time.monotonic() < _audit_skip_until:
        return

    try:
        db.add(AuditLog(session_id=session_id[:128], event=event[:64]))
        db.commit()
    except Exception as exc:
        db.rollback()
        if isinstance(exc, SQLAlchemyError):
            _audit_skip_until = time.monotonic() + _AUDIT_DB_COOLDOWN_SECONDS
        logger.warning("Audit event persistence failed: %s", exc)
