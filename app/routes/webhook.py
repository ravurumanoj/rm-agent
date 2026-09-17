import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.constants import CORRELATION_ID_HEADER
from app.schemas.agent import WebhookEvent
from app.services.local_function_tools import build_default_agent_metadata
from app.services.tracing import operation_span, record_exception
from app.services.unique_webhook import verify_webhook_signature, write_assistant_message
from app.agents.orchestrator import run_turn
from app.utils.logger import logger

router = APIRouter(tags=["Agent"])


_ANSWERABLE_EVENTS = frozenset(
    {
        "unique.chat.external-module.chosen",
        "unique.chat.user-message.created",
    }
)

_EXTERNAL_MODULE_EVENTS = frozenset(
    {
        "unique.chat.external-module.chosen",
    }
)

_WEBHOOK_EXAMPLE: dict[str, Any] = {
    "id": "evt_test_1",
    "version": "1.0.0",
    "event": "unique.chat.external-module.chosen",
    "createdAt": 1700000000,
    "userId": "user_abc",
    "companyId": "company_xyz",
    "payload": {
        "chatId": "chat_test_123",
        "assistantId": "assistant_test_123",
        "text": "How is my portfolio performing this quarter?",
        "userMessage": {"id": "msg_user_1", "text": "How is my portfolio performing this quarter?"},
        "assistantMessage": {"id": "msg_assistant_1"},
        "configuration": {"customerId": "CUST001"},
    },
}


def _response(
    correlation_id: str,
    *,
    success: bool,
    handled: bool | None = None,
    reason: str | None = None,
    status_code: int = 200,
) -> JSONResponse:
    content: dict[str, Any] = {"success": success, "correlation_id": correlation_id}
    if handled is not None:
        content["handled"] = handled
    if reason is not None:
        content["reason"] = reason
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers={CORRELATION_ID_HEADER: correlation_id},
    )


def _build_webhook_metadata(customer_id: str, portfolio_id: str) -> dict[str, Any]:
    return build_default_agent_metadata(
        {
            "client_id": customer_id,
            "active_portfolios": [{"id": portfolio_id}] if portfolio_id else None,
        }
    )


@router.post(
    "/relationship-manager/webhook",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                    "example": _WEBHOOK_EXAMPLE,
                }
            },
        }
    },
)
async def relationship_manager_webhook(request: Request) -> JSONResponse:
    """Unique-compatible webhook endpoint for external-module/user-message events."""
    correlation_id = getattr(request.state, "correlation_id", "")
    with operation_span(
        "http.relationship_manager.webhook",
        kind="CHAIN",
        attributes={
            "http.route": "/relationship-manager/webhook",
            "correlation.id": correlation_id,
        },
    ) as span:
        raw_body = await request.body()
        sig_header = request.headers.get("X-Unique-Signature", "")
        timestamp = request.headers.get("X-Unique-Created-At", "")

        try:
            verify_webhook_signature(raw_body, sig_header, timestamp)
        except RuntimeError as exc:
            logger.warning("Webhook signature verification unavailable", extra={"error": str(exc)})
            record_exception(span, exc)
            if span is not None:
                span.set_attribute("http.status_code", 503)
            return _response(correlation_id, success=False, reason="verification_unavailable", status_code=503)
        except Exception as exc:
            logger.warning("Webhook signature verification failed", extra={"error": str(exc)})
            record_exception(span, exc)
            if span is not None:
                span.set_attribute("http.status_code", 400)
            return _response(correlation_id, success=False, reason="invalid_signature", status_code=400)

        try:
            event = WebhookEvent.model_validate_json(raw_body)
        except Exception as exc:
            logger.warning("Webhook body could not be parsed", extra={"error": str(exc)})
            record_exception(span, exc)
            if span is not None:
                span.set_attribute("http.status_code", 400)
            return _response(correlation_id, success=False, reason="invalid_body", status_code=400)

        if span is not None:
            span.set_attribute("app.webhook.event", event.event)

        if event.event not in _ANSWERABLE_EVENTS:
            if span is not None:
                span.set_attribute("http.status_code", 200)
                span.set_attribute("app.webhook.handled", False)
            return _response(correlation_id, success=True, handled=False)

        expected_module = (settings.UNIQUE_WEBHOOK_EXPECTED_MODULE_NAME or "").strip()
        payload_module_name = str(getattr(event.payload, "name", "") or "").strip()
        if expected_module and event.event in _EXTERNAL_MODULE_EVENTS and payload_module_name != expected_module:
            if span is not None:
                span.set_attribute("http.status_code", 200)
                span.set_attribute("app.webhook.handled", False)
            return _response(correlation_id, success=True, handled=False)

        payload = event.payload
        user_text = (payload.userMessage.text or payload.text).strip()
        chat_id = payload.chatId.strip()

        if not user_text or not chat_id:
            if span is not None:
                span.set_attribute("http.status_code", 200)
                span.set_attribute("app.webhook.handled", False)
            return _response(correlation_id, success=True, handled=False)

        assistant_message_id = payload.assistantMessage.id.strip()

        t0 = time.perf_counter()
        try:
            customer_id = str(payload.configuration.get("customerId") or "").strip()
            webhook_portfolio_id = str(
                payload.configuration.get("portfolioId")
                or payload.configuration.get("portfolio_id")
                or ""
            ).strip()

            webhook_metadata = _build_webhook_metadata(customer_id, webhook_portfolio_id)
            result = await run_turn(
                session_id=chat_id,
                message=user_text,
                metadata=webhook_metadata,
            )
        except Exception as exc:
            logger.exception("Webhook orchestrator run failed", extra={"chat_id": chat_id})
            record_exception(span, exc)
            try:
                write_assistant_message(
                    chat_id=chat_id,
                    message_id=assistant_message_id,
                    text="Sorry, something went wrong while processing your request.",
                    user_id=event.userId,
                    company_id=event.companyId,
                )
            except Exception as fallback_exc:
                logger.exception("Webhook fallback write failed", extra={"chat_id": chat_id})
                record_exception(span, fallback_exc)
            if span is not None:
                span.set_attribute("http.status_code", 200)
                span.set_attribute("app.webhook.handled", True)
                span.set_attribute("app.webhook.success", False)
            return _response(correlation_id, success=False, reason="processing_error")

        try:
            write_assistant_message(
                chat_id=chat_id,
                message_id=assistant_message_id,
                text=result.reply,
                user_id=event.userId,
                company_id=event.companyId,
            )
        except Exception as exc:
            logger.exception("Webhook failed to write assistant message", extra={"chat_id": chat_id})
            record_exception(span, exc)
            if span is not None:
                span.set_attribute("http.status_code", 200)
                span.set_attribute("app.webhook.handled", True)
                span.set_attribute("app.webhook.success", False)
            return _response(correlation_id, success=False, reason="write_error")

        elapsed_ms = round((time.perf_counter() - t0) * 1000)
        logger.info(
            "Webhook handled",
            extra={
                "chat_id": chat_id,
                "elapsed_ms": elapsed_ms,
                "reply_length": len(result.reply),
            },
        )
        if span is not None:
            span.set_attribute("http.status_code", 200)
            span.set_attribute("app.webhook.handled", True)
            span.set_attribute("app.webhook.success", True)
            span.set_attribute("chat.id", chat_id)
            span.set_attribute("app.reply_length", len(result.reply))
        return _response(correlation_id, success=True, handled=True)

