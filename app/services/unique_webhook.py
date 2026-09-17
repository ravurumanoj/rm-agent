from __future__ import annotations

from datetime import datetime, timezone

from app.config import settings
from app.services.tracing import operation_span, record_exception
from app.services.unique_sdk_client import configure_unique_sdk


def verify_webhook_signature(payload: bytes, sig_header: str, timestamp: str) -> bool:
    """Verify Unique webhook signature when explicitly enabled.

    Returns:
        True if signature verification executed, False if skipped.
    """
    with operation_span(
        "unique.webhook.verify_signature",
        kind="TOOL",
        attributes={"app.signature_verification_enabled": settings.UNIQUE_WEBHOOK_VERIFY_SIGNATURE},
    ) as span:
        try:
            if not settings.UNIQUE_WEBHOOK_VERIFY_SIGNATURE:
                if span is not None:
                    span.set_attribute("app.signature_verification_skipped", True)
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
            if span is not None:
                span.set_attribute("tool.success", True)
            return True
        except Exception as exc:
            if span is not None:
                span.set_attribute("tool.success", False)
            record_exception(span, exc)
            raise


def write_assistant_message(
    *,
    chat_id: str,
    message_id: str,
    text: str,
    user_id: str = "",
    company_id: str = "",
) -> None:
    """Write final assistant text back to Unique chat placeholder message."""
    with operation_span(
        "unique.webhook.write_message",
        kind="TOOL",
        attributes={"chat.id": chat_id, "app.has_message_id": bool(message_id)},
    ) as span:
        try:
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
                now_iso = datetime.now(timezone.utc).isoformat()
                modify(
                    user_id=effective_user_id,
                    company_id=effective_company_id,
                    id=message_id,
                    chatId=chat_id,
                    text=text,
                    stoppedStreamingAt=now_iso,
                    completedAt=now_iso,
                )
                if span is not None:
                    span.set_attribute("tool.success", True)
                    span.set_attribute("app.write_mode", "modify")
                return

            if callable(create):
                create(
                    user_id=effective_user_id,
                    company_id=effective_company_id,
                    chatId=chat_id,
                    role="ASSISTANT",
                    text=text,
                )
                if span is not None:
                    span.set_attribute("tool.success", True)
                    span.set_attribute("app.write_mode", "create")
                return

            raise RuntimeError("unique_sdk.Message.modify/create is unavailable in installed SDK")
        except Exception as exc:
            if span is not None:
                span.set_attribute("tool.success", False)
            record_exception(span, exc)
            raise
