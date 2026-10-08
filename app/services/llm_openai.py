from __future__ import annotations

from typing import Any, Optional

from app.config import settings


def _http_clients() -> dict[str, Any]:
    """httpx clients that skip TLS verification (self-signed on-prem endpoints) when SSL_VERIFY=false."""
    if settings.SSL_VERIFY:
        return {}
    import httpx

    return {"http_client": httpx.Client(verify=False), "http_async_client": httpx.AsyncClient(verify=False)}


def create_openai_llm(model: str, bound_tools: Optional[list] = None):
    """Build a chat model for OpenAI or any OpenAI-compatible server (on-prem LLM as a service).

    A custom CA bundle (SSL_CA_CERT_PATH) and proxies are applied through environment variables.
    """
    try:
        from langchain_openai import ChatOpenAI  # type: ignore[reportMissingImports]
    except Exception as exc:
        raise RuntimeError(
            "OpenAI provider unavailable: install optional deps with `pip install -e .[openai]`"
        ) from exc

    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": settings.LLMAAS_TEMPERATURE,
        "max_tokens": settings.LLMAAS_MAX_TOKENS,
        "api_key": settings.LLMAAS_API_KEY,
        **_http_clients(),
    }
    if settings.LLMAAS_BASE_URL:
        kwargs["base_url"] = settings.LLMAAS_BASE_URL

    llm = ChatOpenAI(**kwargs)
    return llm.bind_tools(bound_tools) if bound_tools else llm
