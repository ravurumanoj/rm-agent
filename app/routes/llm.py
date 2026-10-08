"""Endpoints to list the supported LLM models and query one."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.schemas.llm import LLMQueryRequest, LLMQueryResponse, SupportedModelsResponse
from app.services.llm_catalog import LLMCallError, list_supported_models, query_llm_model

router = APIRouter(prefix="/agent/llm", tags=["LLM"])


@router.get("/models", response_model=SupportedModelsResponse)
async def get_supported_models() -> SupportedModelsResponse:
    """Models this app is configured to use."""
    return SupportedModelsResponse(models=list_supported_models())


@router.post("/query", response_model=LLMQueryResponse)
async def query_llm(request: LLMQueryRequest) -> LLMQueryResponse:
    """Send a query to a supported model and return its reply. 400: unsupported model. 502: the model call failed."""
    try:
        return LLMQueryResponse(**await query_llm_model(request.query, request.model))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LLMCallError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
