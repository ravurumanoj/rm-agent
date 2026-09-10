from __future__ import annotations

from typing import Optional

from app.config import settings


def create_gemini_llm(model: str, bound_tools: Optional[list] = None):
    """Build a Gemini chat model with lazy optional dependency import."""
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore[reportMissingImports]
    except Exception as exc:
        raise RuntimeError(
            "Gemini provider unavailable: install optional deps with `pip install -e .[gemini]`"
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
