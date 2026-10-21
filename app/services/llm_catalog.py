"""Supported-model listing and querying the configured LLM."""

from __future__ import annotations

import time
from typing import Any

from langchain_core.messages import HumanMessage

from app.components.messages import extract_text
from app.services.llm import get_llm
from app.services.llm_core import active_provider, build_model_chain, parse_fallback_models, resolve_primary_model
from app.utils.logger import logger, preview


def list_supported_models() -> list[dict[str, Any]]:
    """Models this app is configured to use: the primary first, then any configured fallbacks."""
    provider = active_provider()
    chain = build_model_chain(resolve_primary_model(provider), parse_fallback_models(None))
    return [
        {"model": name, "provider": provider, "is_default": index == 0}
        for index, name in enumerate(chain)
    ]


class LLMCallError(RuntimeError):
    """The model call itself failed (network, auth, provider error)."""


async def query_llm_model(query: str, model: str | None = None) -> dict[str, Any]:
    """Send one query to a supported model and return its reply.

    Raises ValueError for an unsupported model and LLMCallError when the model call fails.
    """
    supported = list_supported_models()
    target = (model or "").strip() or supported[0]["model"]
    names = {item["model"] for item in supported}
    if target not in names:
        raise ValueError(f"Model '{target}' is not supported. Supported models: {', '.join(sorted(names))}")

    started = time.perf_counter()
    try:
        reply = await get_llm(model=target, fallback_models=[]).ainvoke([HumanMessage(content=query)])
    except Exception as exc:
        logger.warning(
            "[LLM] model_query_failed model=%s duration_ms=%s query=%s error=%s: %s",
            target, int((time.perf_counter() - started) * 1000), preview(query), type(exc).__name__, exc,
        )
        raise LLMCallError(f"{type(exc).__name__}: {exc}") from exc
    text = extract_text(reply.content)
    latency_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "[LLM] model_query_completed model=%s latency_ms=%s query=%s response=%s",
        target, latency_ms, preview(query), preview(text),
    )
    return {
        "response": text,
        "model": target,
        "latency_ms": latency_ms,
    }
