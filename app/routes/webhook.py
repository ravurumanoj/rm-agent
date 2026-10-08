import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask

from app.config import settings
from app.constants import CORRELATION_ID_HEADER
from app.schemas.agent import WebhookEvent
from app.services.tracing import operation_span, record_exception
from app.services.unique_chat import (
    StepLogger,
    UniqueChatContext,
    current_step_logger,
    now_iso,
    stream_reply,
    write_assistant_message,
)
from app.services.unique_webhook import is_duplicate_event, verify_webhook_signature
from app.graph.runner import run_turn
from app.utils.logger import logger, preview

router = APIRouter(tags=["Agent"])

_FAILURE_TEXT = "Sorry, something went wrong while processing your request."


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
    background: BackgroundTask | None = None,
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
        background=background,
    )


def _build_webhook_metadata(customer_id: str, portfolio_id: str, ctx: UniqueChatContext) -> dict[str, Any]:
    return {
        "client_id": customer_id,
        "active_portfolios": [{"id": portfolio_id}] if portfolio_id else [],
        # Used by the admin agent to reach the CLM assistant and post back to this chat.
        "user_id": ctx.user_id,
        "company_id": ctx.company_id,
        "rm_assistant_id": ctx.assistant_id,
        "rm_chat_id": ctx.chat_id,
    }


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
            logger.info("Webhook event ignored", extra={"event": event.event, "reason": "unsupported_event"})
            if span is not None:
                span.set_attribute("http.status_code", 200)
                span.set_attribute("app.webhook.handled", False)
            return _response(correlation_id, success=True, handled=False)

        expected_module = (settings.UNIQUE_WEBHOOK_EXPECTED_MODULE_NAME or "").strip()
        payload_module_name = str(getattr(event.payload, "name", "") or "").strip()
        if expected_module and event.event in _EXTERNAL_MODULE_EVENTS and payload_module_name != expected_module:
            logger.info(
                "Webhook event ignored",
                extra={
                    "event": event.event,
                    "reason": "unexpected_module",
                    "expected_module": expected_module,
                    "payload_module_name": payload_module_name,
                },
            )
            if span is not None:
                span.set_attribute("http.status_code", 200)
                span.set_attribute("app.webhook.handled", False)
            return _response(correlation_id, success=True, handled=False)

        payload = event.payload
        user_text = (payload.userMessage.text or payload.text).strip()
        chat_id = payload.chatId.strip()

        if not user_text or not chat_id:
            logger.info(
                "Webhook event ignored",
                extra={
                    "event": event.event,
                    "reason": "missing_chat_or_text",
                    "has_chat_id": bool(chat_id),
                    "has_user_text": bool(user_text),
                },
            )
            if span is not None:
                span.set_attribute("http.status_code", 200)
                span.set_attribute("app.webhook.handled", False)
            return _response(correlation_id, success=True, handled=False)

        if is_duplicate_event(event.id):
            logger.info("Webhook event ignored", extra={"event_id": event.id, "reason": "duplicate_event"})
            if span is not None:
                span.set_attribute("http.status_code", 200)
                span.set_attribute("app.webhook.handled", False)
            return _response(correlation_id, success=True, handled=False, reason="duplicate_event")

        ctx = UniqueChatContext(
            chat_id=chat_id,
            assistant_id=payload.assistantId.strip(),
            message_id=payload.assistantMessage.id.strip(),
            user_id=event.userId,
            company_id=event.companyId,
        )
        customer_id = str(payload.configuration.get("customerId") or "").strip()
        webhook_portfolio_id = str(
            payload.configuration.get("portfolioId") or payload.configuration.get("portfolio_id") or ""
        ).strip()
        metadata = _build_webhook_metadata(customer_id, webhook_portfolio_id, ctx)

        logger.info("Webhook accepted", extra={"chat_id": chat_id, "event": event.event, "user_text": preview(user_text)})
        if span is not None:
            span.set_attribute("http.status_code", 200)
            span.set_attribute("app.webhook.handled", True)
            span.set_attribute("chat.id", chat_id)
        # Answer after responding: Unique retries slow deliveries, and the turn can take far longer than its timeout.
        return _response(
            correlation_id,
            success=True,
            handled=True,
            background=BackgroundTask(_answer_event, ctx, user_text=user_text, metadata=metadata, event_name=event.event),
        )


async def _answer_event(ctx: UniqueChatContext, *, user_text: str, metadata: dict[str, Any], event_name: str) -> None:
    """Run the turn and write the reply (with references) to the Unique chat."""
    steps = StepLogger(ctx)
    token = current_step_logger.set(steps)
    started_at = now_iso()
    t0 = time.perf_counter()
    with operation_span(
        "unique.webhook.turn",
        kind="CHAIN",
        attributes={"chat.id": ctx.chat_id, "app.webhook.event": event_name},
    ) as span:
        try:
            result = await run_turn(session_id=ctx.chat_id, message=user_text, metadata=metadata)
            await stream_reply(ctx, result.reply, result.citations)
            await write_assistant_message(ctx, text=result.reply, citations=result.citations, started_at=started_at)
        except Exception as exc:
            logger.exception("Webhook turn failed", extra={"chat_id": ctx.chat_id})
            record_exception(span, exc)
            await steps.fail_running()
            try:
                await write_assistant_message(ctx, text=_FAILURE_TEXT, started_at=started_at)
            except Exception as fallback_exc:
                logger.exception("Webhook fallback write failed", extra={"chat_id": ctx.chat_id})
                record_exception(span, fallback_exc)
            if span is not None:
                span.set_attribute("app.webhook.success", False)
            return
        finally:
            current_step_logger.reset(token)

        logger.info(
            "Webhook handled",
            extra={
                "chat_id": ctx.chat_id,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000),
                "reply_length": len(result.reply),
                "citation_count": len(result.citations),
                "reply": preview(result.reply),
            },
        )
        if span is not None:
            span.set_attribute("app.webhook.success", True)
            span.set_attribute("app.reply_length", len(result.reply))

