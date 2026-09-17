from fastapi import APIRouter, Depends, HTTPException, Request, status
from starlette.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.agents.orchestrator import run_turn
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
from app.services.local_function_tools import build_default_agent_metadata
from app.services.streaming import stream_chat_events
from app.services.tracing import operation_span, record_exception
from app.services.unique_runtime import list_unique_models, test_unique_model
from app.utils.logger import logger

router = APIRouter(prefix="/agent", tags=["Agent"])


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, db: Session | None = Depends(get_db)) -> ChatResponse:
    """Single-turn entry point into the LangGraph agent."""
    with operation_span(
        "http.agent.chat",
        kind="CHAIN",
        attributes={
            "session.id": request.session_id,
            "http.route": "/agent/chat",
            "app.tool_output_count": len(request.tool_outputs or []),
        },
    ) as span:
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
        except Exception as exc:
            record_event(db, session_id=request.session_id, event="chat_failure")
            logger.exception("[API] chat_failed session_id=%s", request.session_id)
            record_exception(span, exc)
            if span is not None:
                span.set_attribute("http.status_code", 500)
            raise
        record_event(db, session_id=request.session_id, event="chat_response")
        logger.info("[API] chat_completed session_id=%s", request.session_id)
        if span is not None:
            span.set_attribute("http.status_code", 200)
            span.set_attribute("app.citation_count", len(result.citations))
            span.set_attribute("app.evaluation_count", len(result.evaluations))
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
    with operation_span(
        "http.agent.stream",
        kind="CHAIN",
        attributes={
            "session.id": request.session_id,
            "http.route": "/agent/stream",
            "app.tool_output_count": len(request.tool_outputs or []),
        },
    ) as span:
        logger.info("[API] stream_started session_id=%s", request.session_id)
        effective_metadata = build_default_agent_metadata(request.metadata)
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
            except Exception as exc:
                record_exception(span, exc)
                if span is not None:
                    span.set_attribute("app.stream.success", False)
                raise

        if span is not None:
            span.set_attribute("http.status_code", 200)
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


