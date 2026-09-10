from __future__ import annotations

import asyncio
import importlib
from typing import Any, Callable, Optional, Sequence

from app.config import settings
from app.constants import (
    LLM_PROVIDER_GEMINI,
    LLM_PROVIDER_OPENAI,
    LLM_PROVIDER_UNIQUE,
    MODEL_TO_PROVIDER,
)
from app.services.network import configure_network_environment
from app.utils.logger import logger


class ProviderUnavailableError(RuntimeError):
    """Raised when a provider cannot be constructed in the current environment."""


def configure_proxy_env() -> None:
    """Apply proxy settings from config to process environment."""
    configure_network_environment()


def detect_provider(model_name: str) -> str:
    """Resolve model name to provider id."""
    if model_name in MODEL_TO_PROVIDER:
        return MODEL_TO_PROVIDER[model_name]

    lower = model_name.lower()
    if lower.startswith("gemini"):
        return LLM_PROVIDER_GEMINI
    if lower.startswith(("gpt-", "o1-", "o3-", "o4-", "text-")):
        return LLM_PROVIDER_OPENAI
    return LLM_PROVIDER_UNIQUE


def resolve_primary_model(provider: Optional[str] = None, model: Optional[str] = None) -> str:
    """Choose primary model from optional inputs, then config defaults."""
    if model:
        return model

    active_provider = (provider or settings.LLM_PROVIDER).lower().strip()
    if active_provider == LLM_PROVIDER_GEMINI:
        return settings.GEMINI_MODEL
    if active_provider == LLM_PROVIDER_OPENAI:
        return settings.OPENAI_MODEL
    return settings.UNIQUE_MODEL_NAME or LLM_PROVIDER_UNIQUE


def parse_fallback_models(fallback_models: Optional[str | Sequence[str]]) -> list[str]:
    """Parse fallback models from optional string/list; default to config when None."""
    if fallback_models is None:
        return list(settings.LLM_FALLBACK_MODELS_LIST)
    if isinstance(fallback_models, str):
        return [m.strip() for m in fallback_models.split(",") if m.strip()]
    return [m.strip() for m in fallback_models if m and m.strip()]


def build_model_chain(primary: str, fallback_models: Sequence[str]) -> list[str]:
    """Build unique ordered model chain with primary first."""
    deduped: list[str] = []
    for name in [primary, *fallback_models]:
        if not name:
            continue
        if name not in deduped:
            deduped.append(name)
    if not deduped:
        raise RuntimeError("No LLM model configured. Set provider/model settings.")
    return deduped


def _optional_module(module_name: str):
    """Try importing an optional module and return None when absent."""
    try:
        return importlib.import_module(module_name)
    except Exception as exc:
        logger.debug("Optional module '%s' unavailable: %s", module_name, exc)
        return None


def _create_openai_service(model: str, bound_tools: Optional[list] = None):
    local = _optional_module("app.services.llm_openai")
    if local and hasattr(local, "create_openai_llm"):
        return local.create_openai_llm(model, bound_tools)

    try:
        from langchain_openai import ChatOpenAI  # type: ignore[reportMissingImports]
    except Exception as exc:
        raise ProviderUnavailableError(
            "OpenAI provider unavailable: install langchain-openai or add app.services.llm_openai"
        ) from exc

    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": settings.OPENAI_TEMPERATURE,
        "max_tokens": settings.OPENAI_MAX_TOKENS,
        "api_key": settings.OPENAI_API_KEY,
    }
    if settings.OPENAI_BASE_URL:
        kwargs["base_url"] = settings.OPENAI_BASE_URL

    llm = ChatOpenAI(**kwargs)
    if bound_tools:
        return llm.bind_tools(bound_tools)
    return llm


def _create_gemini_service(model: str, bound_tools: Optional[list] = None):
    local = _optional_module("app.services.llm_gemini")
    if local and hasattr(local, "create_gemini_llm"):
        return local.create_gemini_llm(model, bound_tools)

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore[reportMissingImports]
    except Exception as exc:
        raise ProviderUnavailableError(
            "Gemini provider unavailable: install langchain-google-genai or add app.services.llm_gemini"
        ) from exc

    llm = ChatGoogleGenerativeAI(
        model=model,
        temperature=settings.GEMINI_TEMPERATURE,
        max_output_tokens=settings.GEMINI_MAX_TOKENS,
        google_api_key=settings.GOOGLE_API_KEY,
    )
    if bound_tools:
        return llm.bind_tools(bound_tools)
    return llm


def _create_unique_service(model: str, bound_tools: Optional[list] = None):
    local = _optional_module("app.services.llm_unique")
    if local and hasattr(local, "UniqueAILLM"):
        return local.UniqueAILLM(model=model, bound_tools=bound_tools)
    raise ProviderUnavailableError("Unique AI provider unavailable: add app.services.llm_unique")


def create_service_for_model(model: str, bound_tools: Optional[list] = None):
    """Construct provider service lazily for the selected model."""
    provider = detect_provider(model)
    if provider == LLM_PROVIDER_OPENAI:
        return _create_openai_service(model, bound_tools)
    if provider == LLM_PROVIDER_GEMINI:
        return _create_gemini_service(model, bound_tools)
    return _create_unique_service(model, bound_tools)


async def invoke_with_retry(
    coro_fn: Callable[[], Any],
    *,
    max_retries: int,
    base_delay: float,
    backoff_multiplier: float,
    max_delay: float,
    provider_name: str,
) -> Any:
    """Invoke coroutine with exponential backoff retries."""
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_fn()
        except Exception as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            delay = min(base_delay * (backoff_multiplier ** attempt), max_delay)
            logger.warning(
                "%s attempt %s/%s failed (%s: %s). Retrying in %.1fs",
                provider_name,
                attempt + 1,
                max_retries + 1,
                type(exc).__name__,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
    raise last_exc or RuntimeError(f"{provider_name} failed")
