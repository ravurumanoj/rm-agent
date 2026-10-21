from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from app.components.resilience import retry_async
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
    name = active_provider(provider)
    if name == LLM_PROVIDER_OPENAI:
        from app.services.llm_openai import create_openai_llm

        try:
            service = create_openai_llm(model, bound_tools)
        except RuntimeError as exc:
            logger.warning("[LLM] provider_unavailable provider=%s model=%s error=%s", name, model, exc)
            raise ProviderUnavailableError(str(exc)) from exc
    else:
        from app.services.llm_unique import UniqueAILLM

        service = UniqueAILLM(model=model, bound_tools=bound_tools)
    logger.debug(
        "[LLM] client_created provider=%s model=%s bound_tools=%s base_url_set=%s tls_verify=%s",
        name, model, len(bound_tools or []), bool(settings.LLMAAS_BASE_URL) if name == LLM_PROVIDER_OPENAI else "-",
        settings.SSL_VERIFY,
    )
    return service


async def invoke_with_retry(
    coro_fn: Callable[[], Any],
    *,
    max_retries: int,
    base_delay: float,
    backoff_multiplier: float,
    max_delay: float,
    provider_name: str,
) -> Any:
    """Invoke coroutine, retrying only retryable errors with exponential back-off and jitter.

    Non-retryable errors (auth, entitlement, bad request) fail fast; the router then tries the next model.
    """
    return await retry_async(
        coro_fn,
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        multiplier=backoff_multiplier,
        jitter_ratio=settings.RETRY_JITTER_RATIO,
        label=f"LLM {provider_name}",
    )
