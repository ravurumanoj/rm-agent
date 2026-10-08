"""API schemas for the LLM model endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SupportedModel(BaseModel):
    model: str = Field(..., description="Model name as configured (UNIQUE_MODEL_NAME or a fallback).")
    provider: str = Field(..., description="Provider that serves the model.")
    is_default: bool = Field(..., description="True for the primary model used by the agents.")


class SupportedModelsResponse(BaseModel):
    models: list[SupportedModel] = Field(default_factory=list, description="Supported models, primary first.")


class LLMQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Prompt to send to the model.")
    model: str | None = Field(default=None, description="Supported model to query. Defaults to the primary model.")


class LLMQueryResponse(BaseModel):
    response: str = Field(..., description="The model's reply.")
    model: str = Field(..., description="Model that answered.")
    latency_ms: int = Field(..., description="Round-trip time of the call.")
