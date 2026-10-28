from __future__ import annotations

import time

from app.config import settings
from app.services.unique_sdk_client import configure_unique_sdk


def verify_webhook_signature(payload: bytes, sig_header: str, timestamp: str) -> bool:
    """Verify Unique webhook signature when explicitly enabled (not traced: HTTP plumbing; the caller logs it).

    Returns:
        True if signature verification executed, False if skipped.
    """
    if not settings.UNIQUE_WEBHOOK_VERIFY_SIGNATURE:
        return False

    secret = (settings.UNIQUE_WEBHOOK_ENDPOINT_SECRET or "").strip()
    if not secret:
        raise RuntimeError("UNIQUE_WEBHOOK_VERIFY_SIGNATURE=true but UNIQUE_WEBHOOK_ENDPOINT_SECRET is empty")

    sdk = configure_unique_sdk()
    webhook_api = getattr(sdk, "Webhook", None)
    construct_event = getattr(webhook_api, "construct_event", None) if webhook_api else None
    if not callable(construct_event):
        raise RuntimeError("unique_sdk.Webhook.construct_event is unavailable in installed SDK")

    # An invalid signature raises; the webhook answers 400.
    construct_event(payload, sig_header, timestamp, secret)
    return True


_DEDUP_TTL_SECONDS = 600.0
_seen_events: dict[str, float] = {}


def is_duplicate_event(event_id: str) -> bool:
    """True when Unique re-delivers an event we already accepted (it retries on slow or failed responses)."""
    if not event_id:
        return False
    now = time.monotonic()
    for seen_id in [key for key, seen_at in _seen_events.items() if now - seen_at > _DEDUP_TTL_SECONDS]:
        del _seen_events[seen_id]
    if event_id in _seen_events:
        return True
    _seen_events[event_id] = now
    return False
