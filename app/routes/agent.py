import time

from fastapi import APIRouter, Depends, HTTPException, status
from langchain_core.messages import HumanMessage
from starlette.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.components.messages import extract_text
from app.config import settings
from app.graph.runner import run_turn
from app.db.session import get_db
from app.constants import LLM_PROVIDER_ORDER, LLM_PROVIDER_UNIQUE, SSE_RESPONSE_HEADERS
from app.schemas.agent import (
    ChatRequest,
    ChatResponse,
    LLMQueryRequest,
    LLMQueryResponse,
    ModelsResponse,
    ProviderModel,
    ProviderModels,
)
from app.services.audit import record_event
from app.services.llm_core import active_provider, provider_for_model
from app.services.llm_router import get_llm
from app.services.streaming import stream_chat_events
from app.services.unique_runtime import list_unique_models
from app.observability import query_context, record_failure, set_attributes, start_trace
from app.utils.logger import logger, preview

router = APIRouter(prefix="/agent", tags=["Agent"])


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, db: Session | None = Depends(get_db)) -> ChatResponse:
    """Single-turn entry point into the LangGraph agent: one trace per request, rooted here (not the HTTP span)."""
    user_id = str((request.metadata or {}).get("user_id") or "")
    with query_context(session_id=request.session_id, user_id=user_id), start_trace(
        input_value=request.message,
        attributes={"app.entrypoint": "/agent/chat", "app.tool_output_count": len(request.tool_outputs or [])},
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
        except Exception as exc:
            record_event(db, session_id=request.session_id, event="chat_failure")
            logger.exception(
                "[API] chat_failed status=500 session_id=%s duration_ms=%s message=%s",
                request.session_id, int((time.perf_counter() - started) * 1000), preview(request.message),
            )
            record_failure(span, exc, where="turn failed; the API answers 500")
            raise
        record_event(db, session_id=request.session_id, event="chat_response")
        logger.info(
            "[API] chat_completed status=200 session_id=%s duration_ms=%s citations=%s evaluations=%s message=%s reply=%s",
            request.session_id, int((time.perf_counter() - started) * 1000), len(result.citations),
            len(result.evaluations), preview(request.message), preview(result.reply),
        )
        set_attributes(span, {"app.evaluation_count": len(result.evaluations)})
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
    """SSE stream endpoint aligned with wealth app local testing pattern.

    Traced inside the stream (stream_chat_events opens the root span), since the turn runs after this returns.
    """
    logger.debug("[API] stream_accepted session_id=%s message=%s", request.session_id, preview(request.message))
    record_event(db, session_id=request.session_id, event="chat_stream_request")
    return StreamingResponse(
        stream_chat_events(
            session_id=request.session_id,
            message=request.message,
            metadata=request.metadata,
            tool_outputs=request.tool_outputs,
        ),
        media_type="text/event-stream",
        headers=SSE_RESPONSE_HEADERS,
    )


def _configured_models() -> dict[str, list[str]]:
    """Per provider: its model, UNIQUE_MODELS / LLMAAS_MODELS, then the fallback models it serves."""
    configured = {provider: settings.models_for(provider) for provider in LLM_PROVIDER_ORDER}
    for name in settings.LLM_FALLBACK_MODELS_LIST:
        configured[provider_for_model(name)].append(name)
    return {provider: list(dict.fromkeys(names)) for provider, names in configured.items()}


def _provider_models(provider: str, configured: list[str]) -> ProviderModels:
    """Unique: configured models first, then the rest the platform offers. Other providers: config only."""
    if provider != LLM_PROVIDER_UNIQUE:
        return ProviderModels(
            provider=provider, source="config", models=[ProviderModel(model=m, configured=True) for m in configured]
        )
    try:
        platform = [item["model"] for item in list_unique_models()]
    except Exception as exc:
        logger.warning("[API] unique_models_list_failed error=%s: %s", type(exc).__name__, exc)
        return ProviderModels(
            provider=provider,
            source="config",
            models=[ProviderModel(model=m, configured=True) for m in configured],
            error=f"{type(exc).__name__}: {exc}",
        )
    names = dict.fromkeys([*configured, *platform])
    return ProviderModels(
        provider=provider,
        source="unique_platform",
        models=[ProviderModel(model=m, configured=m in configured) for m in names],
    )


@router.get("/llm/models", response_model=ModelsResponse)
def get_models() -> ModelsResponse:
    """Models per provider: Unique lists what its platform offers, openai (LLMaaS) what is configured."""
    default_provider = active_provider()
    configured = _configured_models()
    providers = [_provider_models(provider, configured[provider]) for provider in LLM_PROVIDER_ORDER]
    logger.info(
        "[API] llm_models_listed %s",
        " ".join(f"{item.provider}={len(item.models)}" for item in providers),
    )
    return ModelsResponse(
        default_provider=default_provider,
        default_model=settings.default_model_for(default_provider),
        providers=providers,
    )


@router.post("/llm/query", response_model=LLMQueryResponse)
async def query_model(request: LLMQueryRequest) -> LLMQueryResponse:
    """Test one model with a query; no fallback. 400: unknown provider or no model. 502: the model call failed."""
    try:
        provider = active_provider(request.provider) if request.provider else None
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    model = (request.model or settings.default_model_for(provider)).strip()
    if not model:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No model given and none configured for provider '{provider or settings.LLM_PROVIDER}'.",
        )
    provider = provider or provider_for_model(model)

    started = time.perf_counter()
    try:
        reply = await get_llm(provider=provider, model=model, fallback_models=[]).ainvoke(
            [HumanMessage(content=request.query)]
        )
    except Exception as exc:
        logger.warning(
            "[API] llm_query_failed status=502 provider=%s model=%s duration_ms=%s query=%s error=%s: %s",
            provider, model, int((time.perf_counter() - started) * 1000), preview(request.query),
            type(exc).__name__, exc,
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"{type(exc).__name__}: {exc}") from exc
    text = extract_text(reply.content)
    latency_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "[API] llm_query_completed provider=%s model=%s latency_ms=%s query=%s response=%s",
        provider, model, latency_ms, preview(request.query), preview(text),
    )
    return LLMQueryResponse(response=text, model=model, provider=provider, latency_ms=latency_ms)


