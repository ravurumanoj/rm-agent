from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional, Sequence

from app.config import settings
from app.constants import LLM_PROVIDER_OPENAI, VALID_LLM_PROVIDERS
from app.services.network import configure_network_environment
from app.utils.logger import logger


class ProviderUnavailableError(RuntimeError):
    """Raised when the LLM provider cannot be constructed in the current environment."""


def configure_proxy_env() -> None:
    """Apply proxy settings from config to process environment."""
    configure_network_environment()


def active_provider(provider: Optional[str] = None) -> str:
    """Return the requested provider, else LLM_PROVIDER; unknown values are rejected."""
    name = (provider or settings.LLM_PROVIDER).strip().lower()
    if name not in VALID_LLM_PROVIDERS:
        raise RuntimeError(f"Unsupported LLM_PROVIDER '{name}'. Use one of: {', '.join(sorted(VALID_LLM_PROVIDERS))}.")
    return name


def resolve_primary_model(provider: Optional[str] = None, model: Optional[str] = None) -> str:
    """Return the requested model, else the provider's configured model."""
    return (model or settings.default_model_for(active_provider(provider))).strip()


def parse_fallback_models(fallback_models: Optional[str | Sequence[str]]) -> list[str]:
    """Parse fallback models from optional string/list; default to config when None."""
    if fallback_models is None:
        return list(settings.LLM_FALLBACK_MODELS_LIST)
    if isinstance(fallback_models, str):
        return [m.strip() for m in fallback_models.split(",") if m.strip()]
    return [m.strip() for m in fallback_models if m and m.strip()]


def build_model_chain(primary: str, fallback_models: Sequence[str]) -> list[str]:
    """Build unique ordered model chain with primary first."""
    chain = list(dict.fromkeys(name for name in [primary, *fallback_models] if name))
    if not chain:
        raise RuntimeError("No LLM model configured. Set UNIQUE_MODEL_NAME or LLMAAS_MODEL.")
    return chain


def create_service_for_model(model: str, bound_tools: Optional[list] = None, provider: Optional[str] = None):
    """Construct the chat model for the active provider."""
    if active_provider(provider) == LLM_PROVIDER_OPENAI:
        from app.services.llm_openai import create_openai_llm

        try:
            return create_openai_llm(model, bound_tools)
        except RuntimeError as exc:
            raise ProviderUnavailableError(str(exc)) from exc

    from app.services.llm_unique import UniqueAILLM

    return UniqueAILLM(model=model, bound_tools=bound_tools)


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
