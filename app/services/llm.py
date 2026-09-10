from __future__ import annotations

from typing import Optional, Sequence

from app.config import settings
from app.services.llm_core import (
    ProviderUnavailableError,
    build_model_chain,
    configure_proxy_env,
    detect_provider,
    parse_fallback_models,
    resolve_primary_model,
)
from app.services.llm_router import LLMRouter
from app.services.observability import configure_observability
from app.utils.logger import logger


def get_llm(
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    fallback_models: Optional[str | Sequence[str]] = None,
    bound_tools: Optional[list] = None,
    max_retries: Optional[int] = None,
    base_delay: Optional[float] = None,
    backoff_multiplier: Optional[float] = None,
    max_delay: Optional[float] = None,
) -> LLMRouter:
    """Build a lazy LLM router using optional overrides and config defaults.

    All arguments are optional. If omitted, values are resolved from settings.
    """
    configure_proxy_env()
    configure_observability()

    primary = resolve_primary_model(provider=provider, model=model)
    fallbacks = parse_fallback_models(fallback_models)
    model_chain = build_model_chain(primary, fallbacks)

    logger.info(
        "LLM router initialized with primary=%s%s",
        model_chain[0],
        f", fallbacks={model_chain[1:]}" if len(model_chain) > 1 else ", no fallbacks",
    )

    return LLMRouter(
        models=model_chain,
        bound_tools=bound_tools,
        max_retries=max_retries,
        base_delay=base_delay,
        backoff_multiplier=backoff_multiplier,
        max_delay=max_delay,
    )


__all__ = ["get_llm", "LLMRouter", "ProviderUnavailableError", "detect_provider"]
