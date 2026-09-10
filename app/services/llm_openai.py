from __future__ import annotations

from typing import Any, Optional

from app.config import settings


def create_openai_llm(model: str, bound_tools: Optional[list] = None):
    """Build an OpenAI chat model with lazy optional dependency import."""
    try:
        from langchain_openai import ChatOpenAI  # type: ignore[reportMissingImports]
    except Exception as exc:
        raise RuntimeError(
            "OpenAI provider unavailable: install optional deps with `pip install -e .[openai]`"
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
