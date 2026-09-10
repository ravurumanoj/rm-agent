from __future__ import annotations

from datetime import datetime, timezone

from app.config import settings
from app.services.unique_sdk_client import configure_unique_sdk


def verify_webhook_signature(payload: bytes, sig_header: str, timestamp: str) -> bool:
    """Verify Unique webhook signature when explicitly enabled.

    Returns:
        True if signature verification executed, False if skipped.
    """
    if not settings.UNIQUE_WEBHOOK_VERIFY_SIGNATURE:
        return False

    secret = (settings.UNIQUE_WEBHOOK_ENDPOINT_SECRET or "").strip()
    if not secret:
        raise RuntimeError(
            "UNIQUE_WEBHOOK_VERIFY_SIGNATURE=true but UNIQUE_WEBHOOK_ENDPOINT_SECRET is empty"
        )

    sdk = configure_unique_sdk()
    webhook_api = getattr(sdk, "Webhook", None)
    construct_event = getattr(webhook_api, "construct_event", None) if webhook_api else None
    if not callable(construct_event):
        raise RuntimeError("unique_sdk.Webhook.construct_event is unavailable in installed SDK")

    construct_event(payload, sig_header, timestamp, secret)
    return True


def write_assistant_message(
    *,
    chat_id: str,
    message_id: str,
    text: str,
    user_id: str = "",
    company_id: str = "",
) -> None:
    """Write final assistant text back to Unique chat placeholder message."""
    sdk = configure_unique_sdk()

    effective_user_id = (user_id or settings.UNIQUE_USER_ID or "").strip()
    effective_company_id = (company_id or settings.UNIQUE_COMPANY_ID or "").strip()
    if not effective_user_id or not effective_company_id:
        raise RuntimeError(
            "Unique AI requires company/user identity. Set UNIQUE_COMPANY_ID and "
            "UNIQUE_USER_ID, or pass company_id/user_id per call."
        )

    message_api = getattr(sdk, "Message", None)
    modify = getattr(message_api, "modify", None) if message_api else None
    create = getattr(message_api, "create", None) if message_api else None
    if message_id and callable(modify):
        now = datetime.now(timezone.utc)
        modify(
            user_id=effective_user_id,
            company_id=effective_company_id,
            id=message_id,
            chatId=chat_id,
            text=text,
            stoppedStreamingAt=now,
            completedAt=now,
        )
        return

    if callable(create):
        create(
            user_id=effective_user_id,
            company_id=effective_company_id,
            chatId=chat_id,
            role="ASSISTANT",
            text=text,
        )
        return

    raise RuntimeError("unique_sdk.Message.modify/create is unavailable in installed SDK")
