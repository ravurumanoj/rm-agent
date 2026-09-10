import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.agents.orchestrator import run_turn
from app.config import settings
from app.db.session import get_db
from app.constants import CORRELATION_ID_HEADER, SSE_RESPONSE_HEADERS
from app.schemas.agent import (
    ChatRequest,
    ChatResponse,
    UniqueModelListResponse,
    UniqueModelTestRequest,
    UniqueModelTestResponse,
    WebhookEvent,
)
from app.services.audit import record_event
from app.services.local_function_tools import build_default_agent_metadata
from app.services.sse_listener import format_sse_message, sse_listener
from app.services.streaming import stream_chat_events
from app.services.unique_runtime import list_unique_models, test_unique_model
from app.services.unique_webhook import verify_webhook_signature, write_assistant_message
from app.utils.logger import logger

router = APIRouter(prefix="/agent", tags=["Agent"])
webhook_router = APIRouter(tags=["Agent"])


_ANSWERABLE_EVENTS = frozenset(
    {
        "unique.chat.external-module.chosen",
        "unique.chat.user-message.created",
        "module.chosen",
        "user.message.created",
        "external-module.chosen",
        "user-message.created",
    }
)

_EXTERNAL_MODULE_EVENTS = frozenset(
    {
        "unique.chat.external-module.chosen",
        "external-module.chosen",
        "module.chosen",
    }
)

_WEBHOOK_EXAMPLE: dict[str, Any] = {
    "id": "evt_test_1",
    "version": "1.0.0",
    "event": "module.chosen",
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


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, db: Session | None = Depends(get_db)) -> ChatResponse:
    """Single-turn entry point into the LangGraph agent."""
    logger.info("[API] chat_started session_id=%s", request.session_id)
    effective_metadata = build_default_agent_metadata(request.metadata)
    record_event(db, session_id=request.session_id, event="chat_request")
    try:
        result = await run_turn(
            session_id=request.session_id,
            message=request.message,
            metadata=effective_metadata,
            tool_outputs=request.tool_outputs,
        )
    except Exception:
        record_event(db, session_id=request.session_id, event="chat_failure")
        logger.exception("[API] chat_failed session_id=%s", request.session_id)
        raise
    record_event(db, session_id=request.session_id, event="chat_response")
    logger.info("[API] chat_completed session_id=%s", request.session_id)
    return ChatResponse(
        session_id=request.session_id,
        reply=result.reply,
        citations=result.citations,
        evaluations=result.evaluations,
    )


@router.post(
    "/stream",
    summary="Stream the agent response via Server-Sent Events.",
    response_description="text/event-stream with step/token/done/error event envelopes",
)
async def stream_chat(request: ChatRequest, db: Session | None = Depends(get_db)) -> StreamingResponse:
    """SSE stream endpoint aligned with wealth app local testing pattern."""
    logger.info("[API] stream_started session_id=%s", request.session_id)
    effective_metadata = build_default_agent_metadata(request.metadata)
    record_event(db, session_id=request.session_id, event="chat_stream_request")

    async def event_generator():
        async for payload in stream_chat_events(
            session_id=request.session_id,
            message=request.message,
            metadata=effective_metadata,
            tool_outputs=request.tool_outputs,
        ):
            yield payload

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=SSE_RESPONSE_HEADERS,
    )


@router.get("/unique/models", response_model=UniqueModelListResponse)
async def get_unique_models() -> UniqueModelListResponse:
    """List available LLM model names from Unique."""
    logger.info("[API] unique_models_list_started")
    try:
        models = list_unique_models()
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list Unique models: {exc}",
        ) from exc

    logger.info("[API] unique_models_list_completed count=%s", len(models))
    return UniqueModelListResponse(models=models)


@router.post("/unique/models/test", response_model=UniqueModelTestResponse)
async def run_unique_model_test(request: UniqueModelTestRequest) -> UniqueModelTestResponse:
    """Invoke a specific Unique model with a test query."""
    logger.info("[API] unique_model_test_started model=%s", request.model)
    try:
        response_text = await test_unique_model(
            model=request.model,
            query=request.query,
            company_id=request.company_id,
            user_id=request.user_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unique model test failed: {exc}",
        ) from exc

    logger.info("[API] unique_model_test_completed model=%s response_length=%s", request.model, len(response_text))
    return UniqueModelTestResponse(
        model=request.model,
        query=request.query,
        response=response_text,
    )


@webhook_router.post(
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
    raw_body = await request.body()
    sig_header = request.headers.get("X-Unique-Signature", "")
    timestamp = request.headers.get("X-Unique-Created-At", "")
    await sse_listener.publish(
        event="webhook_received",
        data={
            "path": "/relationship-manager/webhook",
            "has_signature": bool(sig_header),
            "correlation_id": correlation_id,
        },
    )

    try:
        signature_verified = verify_webhook_signature(raw_body, sig_header, timestamp)
    except RuntimeError as exc:
        await sse_listener.publish(
            event="webhook_rejected",
            data={"reason": "verification_unavailable", "error": str(exc), "correlation_id": correlation_id},
        )
        logger.warning("Webhook signature verification unavailable", extra={"error": str(exc)})
        return JSONResponse(
            status_code=503,
            content={"success": False, "reason": "verification_unavailable", "correlation_id": correlation_id},
            headers={CORRELATION_ID_HEADER: correlation_id},
        )
    except Exception as exc:
        await sse_listener.publish(
            event="webhook_rejected",
            data={"reason": "invalid_signature", "error": str(exc), "correlation_id": correlation_id},
        )
        logger.warning("Webhook signature verification failed", extra={"error": str(exc)})
        return JSONResponse(
            status_code=400,
            content={"success": False, "reason": "invalid_signature", "correlation_id": correlation_id},
            headers={CORRELATION_ID_HEADER: correlation_id},
        )
    else:
        await sse_listener.publish(
            event="webhook_signature",
            data={
                "verified": bool(signature_verified),
                "mode": "enforced" if signature_verified else "skipped",
                "correlation_id": correlation_id,
            },
        )

    try:
        event = WebhookEvent.model_validate_json(raw_body)
    except Exception as exc:
        await sse_listener.publish(
            event="webhook_rejected",
            data={"reason": "invalid_body", "error": str(exc), "correlation_id": correlation_id},
        )
        logger.warning("Webhook body could not be parsed", extra={"error": str(exc)})
        return JSONResponse(
            status_code=400,
            content={"success": False, "reason": "invalid_body", "correlation_id": correlation_id},
            headers={CORRELATION_ID_HEADER: correlation_id},
        )

    await sse_listener.publish(
        event="webhook_parsed",
        chat_id=event.payload.chatId.strip(),
        data={
            "event": event.event,
            "chat_id": event.payload.chatId,
            "has_assistant_message": bool(event.payload.assistantMessage.id),
            "correlation_id": correlation_id,
        },
    )

    if event.event not in _ANSWERABLE_EVENTS:
        await sse_listener.publish(
            event="webhook_ignored",
            chat_id=event.payload.chatId.strip(),
            data={"reason": "not_answerable", "event": event.event, "correlation_id": correlation_id},
        )
        return JSONResponse(
            status_code=200,
            content={"success": True, "handled": False, "correlation_id": correlation_id},
            headers={CORRELATION_ID_HEADER: correlation_id},
        )

    expected_module = (settings.UNIQUE_WEBHOOK_EXPECTED_MODULE_NAME or "").strip()
    payload_module_name = str(getattr(event.payload, "name", "") or "").strip()
    if expected_module and event.event in _EXTERNAL_MODULE_EVENTS and payload_module_name != expected_module:
        await sse_listener.publish(
            event="webhook_ignored",
            chat_id=event.payload.chatId.strip(),
            data={
                "reason": "module_name_mismatch",
                "expected_module": expected_module,
                "received_module": payload_module_name,
                "event": event.event,
                "correlation_id": correlation_id,
            },
        )
        return JSONResponse(
            status_code=200,
            content={"success": True, "handled": False, "correlation_id": correlation_id},
            headers={CORRELATION_ID_HEADER: correlation_id},
        )

    payload = event.payload
    user_text = (payload.userMessage.text or payload.text).strip()
    chat_id = payload.chatId.strip()
    assistant_message_id = payload.assistantMessage.id.strip()

    if not user_text or not chat_id:
        await sse_listener.publish(
            event="webhook_ignored",
            chat_id=chat_id,
            data={
                "reason": "missing_chat_or_text",
                "has_text": bool(user_text),
                "has_chat_id": bool(chat_id),
                "correlation_id": correlation_id,
            },
        )
        return JSONResponse(
            status_code=200,
            content={"success": True, "handled": False, "correlation_id": correlation_id},
            headers={CORRELATION_ID_HEADER: correlation_id},
        )

    t0 = time.perf_counter()
    await sse_listener.publish(
        event="orchestrator_started",
        chat_id=chat_id,
        data={"message_preview": user_text[:160], "correlation_id": correlation_id},
    )
    try:
        customer_id = str(payload.configuration.get("customerId") or "").strip()
        webhook_portfolio_id = str(
            payload.configuration.get("portfolioId")
            or payload.configuration.get("portfolio_id")
            or ""
        ).strip()

        webhook_metadata = build_default_agent_metadata(
            {
                "client_id": customer_id,
                "active_portfolios": [{"id": webhook_portfolio_id}] if webhook_portfolio_id else None,
            }
        )
        result = await run_turn(
            session_id=chat_id,
            message=user_text,
            metadata=webhook_metadata,
        )
    except Exception:
        await sse_listener.publish(
            event="orchestrator_failed",
            chat_id=chat_id,
            data={"reason": "processing_error", "correlation_id": correlation_id},
        )
        logger.exception("Webhook orchestrator run failed", extra={"chat_id": chat_id})
        try:
            write_assistant_message(
                chat_id=chat_id,
                message_id=assistant_message_id,
                text="Sorry, something went wrong while processing your request.",
                user_id=event.userId,
                company_id=event.companyId,
            )
        except Exception:
            logger.exception("Webhook fallback write failed", extra={"chat_id": chat_id})
        return JSONResponse(
            status_code=200,
            content={"success": False, "reason": "processing_error", "correlation_id": correlation_id},
            headers={CORRELATION_ID_HEADER: correlation_id},
        )

    await sse_listener.publish(
        event="orchestrator_completed",
        chat_id=chat_id,
        data={
            "reply_preview": result.reply[:200],
            "reply_length": len(result.reply),
            "correlation_id": correlation_id,
        },
    )

    try:
        write_assistant_message(
            chat_id=chat_id,
            message_id=assistant_message_id,
            text=result.reply,
            user_id=event.userId,
            company_id=event.companyId,
        )
    except Exception:
        await sse_listener.publish(
            event="webhook_writeback_failed",
            chat_id=chat_id,
            data={"reason": "write_error", "correlation_id": correlation_id},
        )
        logger.exception("Webhook failed to write assistant message", extra={"chat_id": chat_id})
        return JSONResponse(
            status_code=200,
            content={"success": False, "reason": "write_error", "correlation_id": correlation_id},
            headers={CORRELATION_ID_HEADER: correlation_id},
        )

    elapsed_ms = round((time.perf_counter() - t0) * 1000)
    await sse_listener.publish(
        event="webhook_handled",
        chat_id=chat_id,
        data={"elapsed_ms": elapsed_ms, "reply_length": len(result.reply), "correlation_id": correlation_id},
    )
    logger.info(
        "Webhook handled",
        extra={
            "chat_id": chat_id,
            "elapsed_ms": elapsed_ms,
            "reply_length": len(result.reply),
        },
    )
    return JSONResponse(
        status_code=200,
        content={"success": True, "handled": True, "correlation_id": correlation_id},
        headers={CORRELATION_ID_HEADER: correlation_id},
    )


@webhook_router.get("/relationship-manager/webhook/events")
async def relationship_manager_webhook_events(chat_id: str = "") -> StreamingResponse:
    """SSE stream for local webhook lifecycle visibility during Unique UI testing."""

    async def event_stream():
        async for item in sse_listener.subscribe(chat_id=chat_id.strip(), replay_last=20):
            yield format_sse_message(item)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@webhook_router.get("/relationship-manager/webhook/events/recent")
async def relationship_manager_webhook_events_recent(chat_id: str = "", limit: int = 100) -> dict:
    """Fetch recent webhook lifecycle events for quick local debugging."""
    events = await sse_listener.list_recent(chat_id=chat_id.strip(), limit=limit)
    return {"count": len(events), "events": events}
