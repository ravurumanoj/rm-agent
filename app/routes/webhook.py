import asyncio
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask

from app.config import settings
from app.constants import CORRELATION_ID_HEADER, FALLBACK_TURN_FAILURE
from app.schemas.agent import WebhookEvent
from app.services.chat_history import build_unique_history_block
from app.observability import (
    SpanKind,
    SpanName,
    query_context,
    record_failure,
    set_attributes,
    start_span,
    start_trace,
)
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
from app.services.llm_router import use_primary_model
from app.services.llm_unique import use_unique_identity
from app.utils.logger import logger, preview

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

# Filled values are needed to answer; empty ones are optional. userId/companyId fall back to UNIQUE_USER_ID/
# UNIQUE_COMPANY_ID, and the reply goes into assistantMessage.id when given, else a new message for assistantId.
_WEBHOOK_EXAMPLE: dict[str, Any] = {
    "id": "",
    "version": "",
    "event": "unique.chat.external-module.chosen",
    "createdAt": "",
    "userId": "user_abc",
    "companyId": "company_xyz",
    "payload": {
        "chatId": "chat_test_123",
        "assistantId": "assistant_test_123",
        "text": "",
        "userMessage": {"id": "", "text": "How is my portfolio performing this quarter?"},
        "assistantMessage": {"id": ""},
        "configuration": {"portfolioId": ""},
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


def _ignored(correlation_id: str, event: Any, reason: str, detail: str = "", response_reason: str | None = None) -> JSONResponse:
    """A valid delivery this module does not answer: logged once with the reason, acknowledged with 200."""
    logger.info("[WEBHOOK] ignored reason=%s event=%s event_id=%s %s", reason, event.event, event.id, detail)
    return _response(correlation_id, success=True, handled=False, reason=response_reason)


def _requested_model(payload: Any) -> str:
    """Model name from the payload's modelChoice (a name or an object with "name"); empty when not sent."""
    choice = payload.modelChoice
    if isinstance(choice, dict):
        choice = choice.get("name")
    return str(choice or "").strip()


def _build_webhook_metadata(customer_id: str, portfolio_id: str, ctx: UniqueChatContext) -> dict[str, Any]:
    return {
        "client_id": customer_id,
        "active_portfolios": [{"id": portfolio_id}] if portfolio_id else [],
        # Used by the admin agent to reach the CLM assistant as this user.
        "user_id": ctx.user_id,
        "company_id": ctx.company_id,
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
    """Unique-compatible webhook endpoint for external-module/user-message events.

    The HTTP delivery is not traced (logs cover it); the turn it starts is one trace of its own, see _answer_event.
    """
    correlation_id = getattr(request.state, "correlation_id", "")
    raw_body = await request.body()
    sig_header = request.headers.get("X-Unique-Signature", "")
    timestamp = request.headers.get("X-Unique-Created-At", "")

    try:
        verify_webhook_signature(raw_body, sig_header, timestamp)
    except RuntimeError as exc:
        logger.warning("[WEBHOOK] rejected status=503 reason=verification_unavailable error=%s", exc)
        return _response(correlation_id, success=False, reason="verification_unavailable", status_code=503)
    except Exception as exc:
        logger.warning("[WEBHOOK] rejected status=400 reason=invalid_signature error=%s", exc)
        return _response(correlation_id, success=False, reason="invalid_signature", status_code=400)

    try:
        event = WebhookEvent.model_validate_json(raw_body)
    except Exception as exc:
        logger.warning("[WEBHOOK] rejected status=400 reason=invalid_body bytes=%s error=%s", len(raw_body), exc)
        return _response(correlation_id, success=False, reason="invalid_body", status_code=400)

    if event.event not in _ANSWERABLE_EVENTS:
        return _ignored(correlation_id, event, "unsupported_event")

    expected_module = (settings.UNIQUE_WEBHOOK_EXPECTED_MODULE_NAME or "").strip()
    payload_module_name = str(getattr(event.payload, "name", "") or "").strip()
    if expected_module and event.event in _EXTERNAL_MODULE_EVENTS and payload_module_name != expected_module:
        return _ignored(
            correlation_id, event, "unexpected_module", f"expected={expected_module} got={payload_module_name}"
        )

    payload = event.payload
    user_text = (payload.userMessage.text or payload.text).strip()
    chat_id = payload.chatId.strip()

    if not user_text or not chat_id:
        return _ignored(
            correlation_id, event, "missing_chat_or_text", f"has_chat_id={bool(chat_id)} has_text={bool(user_text)}"
        )

    if is_duplicate_event(event.id):
        return _ignored(correlation_id, event, "duplicate_event", response_reason="duplicate_event")

    ctx = UniqueChatContext(
        chat_id=chat_id,
        assistant_id=payload.assistantId.strip(),
        message_id=payload.assistantMessage.id.strip(),
        user_id=event.userId,
        company_id=event.companyId,
        user_message_id=payload.userMessage.id.strip(),
    )
    customer_id = str(payload.configuration.get("customerId") or "").strip()
    webhook_portfolio_id = str(
        payload.configuration.get("portfolioId") or payload.configuration.get("portfolio_id") or ""
    ).strip()
    metadata = _build_webhook_metadata(customer_id, webhook_portfolio_id, ctx)
    model = _requested_model(payload)

    logger.info(
        "[WEBHOOK] accepted event=%s event_id=%s chat_id=%s user_id=%s customer_id=%s model=%s user_text=%s",
        event.event, event.id, chat_id, event.userId, customer_id or "-", model or "-", preview(user_text),
    )
    # Answer after responding: Unique retries slow deliveries, and the turn can take far longer than its timeout.
    return _response(
        correlation_id,
        success=True,
        handled=True,
        background=BackgroundTask(
            _answer_event, ctx, user_text=user_text, metadata=metadata, event_name=event.event, model=model
        ),
    )


async def _answer_event(
    ctx: UniqueChatContext, *, user_text: str, metadata: dict[str, Any], event_name: str, model: str = ""
) -> None:
    """Run the turn and write the reply (with references) to the Unique chat."""
    steps = StepLogger(ctx)
    token = current_step_logger.set(steps)
    started_at = now_iso()
    t0 = time.perf_counter()
    # One trace per query: identity first, then the root span, a new trace (not under the HTTP request this task
    # inherits) that covers the whole turn: history load, graph and reply delivery, so its latency is the turn's.
    # The payload's model (if any) is the primary for every LLM call in this turn; configured models stay as fallbacks.
    # The event's userId/companyId are the identity of every Unique LLM call in this turn; settings stay as fallbacks.
    with use_primary_model(model), use_unique_identity(ctx.user_id, ctx.company_id), query_context(
        session_id=ctx.chat_id, user_id=ctx.user_id
    ), start_trace(
        input_value=user_text,
        attributes={"app.webhook.event": event_name, "app.llm.requested_model": model or "-"},
    ) as span:
        # First thing in the turn: fetch and summarise the chat history while the turn is being set up.
        history_task = asyncio.create_task(build_unique_history_block(ctx))
        try:
            # Empty when unavailable: run_turn then falls back to the in-process history.
            history_block = await history_task
            result = await run_turn(
                session_id=ctx.chat_id, message=user_text, metadata=metadata, history_block=history_block
            )
            with start_span(
                SpanName.step("send_reply"), kind=SpanKind.CHAIN, attributes={"app.reply_length": len(result.reply)}
            ):
                await stream_reply(ctx, result.reply, result.citations)
                await write_assistant_message(ctx, text=result.reply, citations=result.citations, started_at=started_at)
        except Exception as exc:
            logger.exception(
                "[WEBHOOK] turn_failed chat_id=%s event=%s duration_ms=%s; sending the failure reply",
                ctx.chat_id, event_name, round((time.perf_counter() - t0) * 1000),
            )
            record_failure(span, exc, where="turn failed; the RM is sent the failure reply")
            await steps.fail_running()
            try:
                await write_assistant_message(ctx, text=FALLBACK_TURN_FAILURE, started_at=started_at)
            except Exception as fallback_exc:
                logger.exception("[WEBHOOK] failure_reply_write_failed chat_id=%s; the RM sees no reply", ctx.chat_id)
                record_failure(span, fallback_exc, where="turn failed and the failure reply could not be written")
            set_attributes(span, {"app.webhook.success": False})
            return
        finally:
            current_step_logger.reset(token)

        logger.info(
            "[WEBHOOK] handled chat_id=%s event=%s duration_ms=%s history_chars=%s reply_length=%s citations=%s reply=%s",
            ctx.chat_id, event_name, round((time.perf_counter() - t0) * 1000), len(history_block), len(result.reply),
            len(result.citations), preview(result.reply),
        )
        set_attributes(span, {"app.webhook.success": True, "app.reply_length": len(result.reply)})

