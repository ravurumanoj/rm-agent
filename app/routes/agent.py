import time

from fastapi import APIRouter, Depends, HTTPException, status
from starlette.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.graph.runner import run_turn
from app.db.session import get_db
from app.constants import SSE_RESPONSE_HEADERS
from app.schemas.agent import (
    ChatRequest,
    ChatResponse,
    UniqueModelListResponse,
    UniqueModelTestRequest,
    UniqueModelTestResponse,
)
from app.services.audit import record_event
from app.services.streaming import stream_chat_events
from app.observability import Attr, SpanKind, SpanName, set_attributes, start_span
from app.services.unique_runtime import list_unique_models, test_unique_model
from app.utils.logger import logger, preview

router = APIRouter(prefix="/agent", tags=["Agent"])


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, db: Session | None = Depends(get_db)) -> ChatResponse:
    """Single-turn entry point into the LangGraph agent."""
    with start_span(
        SpanName.http("/agent/chat"),
        kind=SpanKind.CHAIN,
        attributes={
            Attr.SESSION_ID: request.session_id,
            "http.route": "/agent/chat",
            "app.tool_output_count": len(request.tool_outputs or []),
        },
    ) as span:
        started = time.perf_counter()
        effective_metadata = request.metadata
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
            logger.exception(
                "[API] chat_failed status=500 session_id=%s duration_ms=%s message=%s",
                request.session_id, int((time.perf_counter() - started) * 1000), preview(request.message),
            )
            set_attributes(span, {"http.status_code": 500})
            raise
        record_event(db, session_id=request.session_id, event="chat_response")
        logger.info(
            "[API] chat_completed status=200 session_id=%s duration_ms=%s citations=%s evaluations=%s message=%s reply=%s",
            request.session_id, int((time.perf_counter() - started) * 1000), len(result.citations),
            len(result.evaluations), preview(request.message), preview(result.reply),
        )
        set_attributes(
            span,
            {
                "http.status_code": 200,
                Attr.CITATION_COUNT: len(result.citations),
                "app.evaluation_count": len(result.evaluations),
            },
        )
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
    with start_span(
        SpanName.http("/agent/stream"),
        kind=SpanKind.CHAIN,
        attributes={
            Attr.SESSION_ID: request.session_id,
            "http.route": "/agent/stream",
            "app.tool_output_count": len(request.tool_outputs or []),
        },
    ) as span:
        logger.debug("[API] stream_accepted session_id=%s message=%s", request.session_id, preview(request.message))
        effective_metadata = request.metadata
        record_event(db, session_id=request.session_id, event="chat_stream_request")

        async def event_generator():
            try:
                async for payload in stream_chat_events(
                    session_id=request.session_id,
                    message=request.message,
                    metadata=effective_metadata,
                    tool_outputs=request.tool_outputs,
                ):
                    yield payload
            except Exception:
                set_attributes(span, {"app.stream.success": False})
                raise

        set_attributes(span, {"http.status_code": 200})
        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers=SSE_RESPONSE_HEADERS,
        )


@router.get("/unique/models", response_model=UniqueModelListResponse)
async def get_unique_models() -> UniqueModelListResponse:
    """List available LLM model names from Unique."""
    try:
        models = list_unique_models()
    except RuntimeError as exc:
        logger.warning("[API] unique_models_list_unavailable error=%s", exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("[API] unique_models_list_failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list Unique models: {exc}",
        ) from exc

    logger.info("[API] unique_models_list_completed count=%s", len(models))
    return UniqueModelListResponse(models=models)


@router.post("/unique/models/test", response_model=UniqueModelTestResponse)
async def run_unique_model_test(request: UniqueModelTestRequest) -> UniqueModelTestResponse:
    """Invoke a specific Unique model with a test query."""
    try:
        response_text = await test_unique_model(
            model=request.model,
            query=request.query,
            company_id=request.company_id,
            user_id=request.user_id,
        )
    except RuntimeError as exc:
        logger.warning("[API] unique_model_test_rejected model=%s error=%s", request.model, exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("[API] unique_model_test_failed model=%s", request.model)
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


