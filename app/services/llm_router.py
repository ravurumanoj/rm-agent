from __future__ import annotations

import asyncio
import functools
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator, Callable, Iterator, Optional

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage

from app.components.messages import extract_text
from app.config import settings
from app.services.llm_core import (
    ProviderUnavailableError,
    create_service_for_model,
    invoke_with_retry,
)
from app.components.llm_call_context import current_llm_call_name
from app.observability import normalize_token_usage, record_llm_response, set_attributes, start_llm_span
from app.utils.logger import logger, preview


def token_usage(result: Any) -> dict[str, int] | None:
    """Provider-reported usage from a LangChain message (usage_metadata or response_metadata), if any."""
    metadata = getattr(result, "response_metadata", None) or {}
    for candidate in (getattr(result, "usage_metadata", None), metadata.get("token_usage"), metadata.get("usage")):
        usage = normalize_token_usage(candidate)
        if usage:
            return usage
    return None


# Model requested for the current turn (e.g. the webhook's modelChoice); empty means use the configured chain.
_requested_model: ContextVar[str] = ContextVar("llm_requested_model", default="")


@contextmanager
def use_primary_model(model: str) -> Iterator[None]:
    """Make `model` the primary for every LLM call inside the block; the configured models stay as fallbacks."""
    token = _requested_model.set((model or "").strip())
    try:
        yield
    finally:
        _requested_model.reset(token)


class LLMRouter:
    """Route LLM calls across a model chain with fallback, all served by one provider."""

    def __init__(
        self,
        models: list[str],
        bound_tools: Optional[list] = None,
        service_factory: Optional[Callable] = None,
        max_retries: Optional[int] = None,
        base_delay: Optional[float] = None,
        backoff_multiplier: Optional[float] = None,
        max_delay: Optional[float] = None,
        provider: Optional[str] = None,
    ) -> None:
        self._models = list(models)
        self._bound_tools = list(bound_tools or [])
        self._cache: dict[str, Any] = {}
        self._provider = (provider or settings.LLM_PROVIDER).strip().lower()
        self._service_factory = service_factory or functools.partial(create_service_for_model, provider=self._provider)
        self._max_retries = settings.LLM_MAX_RETRIES if max_retries is None else max_retries
        self._base_delay = settings.LLM_RETRY_BASE_DELAY if base_delay is None else base_delay
        self._backoff_multiplier = (
            settings.LLM_RETRY_BACKOFF_MULTIPLIER if backoff_multiplier is None else backoff_multiplier
        )
        self._max_delay = settings.LLM_RETRY_MAX_DELAY if max_delay is None else max_delay

    def _chain(self) -> list[str]:
        """Models to try for this call: the turn's requested model first (if any), then the configured chain."""
        requested = _requested_model.get()
        if not requested:
            return self._models
        return list(dict.fromkeys([requested, *self._models]))

    def _svc(self, model: str):
        if model not in self._cache:
            self._cache[model] = self._service_factory(model, self._bound_tools or None)
        return self._cache[model]

    async def _invoke_one(self, model: str, messages: list[BaseMessage], **kwargs) -> AIMessage:
        svc = self._svc(model)
        provider = self._provider
        started = time.perf_counter()
        # One LLM span per call (provider clients add details to it); a failure propagating out marks it failed.
        with start_llm_span(
            caller=current_llm_call_name(),
            model=model,
            provider=provider,
            messages=messages,
            attributes={"app.llm.max_retries": self._max_retries, "app.llm.fallback_model": model != self._chain()[0]},
        ) as span:
            if getattr(svc, "HANDLES_OWN_RETRY", False):
                result = await svc.ainvoke(messages, **kwargs)
            else:
                result = await invoke_with_retry(
                    lambda: svc.ainvoke(messages, **kwargs),
                    max_retries=self._max_retries,
                    base_delay=self._base_delay,
                    backoff_multiplier=self._backoff_multiplier,
                    max_delay=self._max_delay,
                    provider_name=model,
                )
            record_llm_response(span, output=result.content, usage=token_usage(result), messages=messages)
            set_attributes(span, {"app.llm.tool_call_count": len(getattr(result, "tool_calls", None) or [])})
            usage = token_usage(result)
            logger.info(
                "[LLM] call_completed call=%s model=%s duration_ms=%s messages=%s tool_calls=%s tokens=%s output=%s",
                current_llm_call_name() or "-", model, int((time.perf_counter() - started) * 1000), len(messages),
                len(getattr(result, "tool_calls", None) or []), usage or "not reported", preview(extract_text(result.content)),
            )
            return result

    async def ainvoke(self, messages: list[BaseMessage], **kwargs) -> AIMessage:
        last_exc: Exception | None = None
        models = self._chain()
        for model in models:
            try:
                result = await self._invoke_one(model, messages, **kwargs)
                if model != models[0]:
                    logger.warning("[LLM] fallback_used call=%s model=%s primary=%s", current_llm_call_name() or "-", model, models[0])
                return result
            except ProviderUnavailableError as exc:
                logger.warning("[LLM] provider_unavailable call=%s model=%s error=%s", current_llm_call_name() or "-", model, exc)
                last_exc = exc
            except Exception as exc:
                logger.warning(
                    "[LLM] model_failed call=%s model=%s error=%s: %s; %s",
                    current_llm_call_name() or "-", model, type(exc).__name__, exc,
                    "trying the next model" if model != models[-1] else "no model left",
                )
                last_exc = exc
        logger.error("[LLM] all_models_failed call=%s models=%s", current_llm_call_name() or "-", models)
        raise last_exc or RuntimeError("All configured LLM models failed")

    async def astream(self, messages: list[BaseMessage], **kwargs) -> AsyncIterator[AIMessageChunk]:
        last_exc: Exception | None = None
        models = self._chain()
        for model in models:
            yielded_any = False
            provider = self._provider
            try:
                started = time.perf_counter()
                with start_llm_span(
                    caller=current_llm_call_name(),
                    model=model,
                    provider=provider,
                    messages=messages,
                    attributes={"app.llm.stream": True, "app.llm.fallback_model": model != models[0]},
                ) as span:
                    collected_chunks: list[str] = []
                    async for chunk in self._svc(model).astream(messages, **kwargs):
                        content = getattr(chunk, "content", "")
                        if content and not collected_chunks:
                            ttft_ms = int((time.perf_counter() - started) * 1000)
                            set_attributes(span, {"app.llm.time_to_first_token_ms": ttft_ms})
                        if content:
                            collected_chunks.append(content if isinstance(content, str) else str(content))
                        yielded_any = True
                        yield chunk
                    if collected_chunks:
                        text = "".join(collected_chunks)
                        record_llm_response(span, output=text, usage=token_usage(chunk), messages=messages)
                        logger.info(
                            "[LLM] stream_completed call=%s model=%s duration_ms=%s fallback=%s output=%s",
                            current_llm_call_name() or "-", model, int((time.perf_counter() - started) * 1000),
                            model != models[0], preview(text),
                        )
                return
            except ProviderUnavailableError as exc:
                logger.warning("[LLM] provider_unavailable call=%s model=%s error=%s", current_llm_call_name() or "-", model, exc)
                last_exc = exc
            except Exception as exc:
                if yielded_any:
                    logger.error("[LLM] stream_failed_mid_output call=%s model=%s error=%s", current_llm_call_name() or "-", model, exc)
                    raise
                logger.warning(
                    "[LLM] stream_failed_before_output call=%s model=%s error=%s: %s",
                    current_llm_call_name() or "-", model, type(exc).__name__, exc,
                )
                last_exc = exc
        raise last_exc or RuntimeError("All configured LLM models failed during streaming")

    def invoke(self, messages: list[BaseMessage], **kwargs) -> AIMessage:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            raise RuntimeError("LLMRouter.invoke() cannot run inside an active event loop; use ainvoke().")
        return asyncio.run(self.ainvoke(messages, **kwargs))

    def with_structured_output(self, schema: Any, **kwargs):
        svc = self._svc(self._chain()[0])
        if not hasattr(svc, "with_structured_output"):
            raise AttributeError("Primary provider does not support structured output")
        return svc.with_structured_output(schema, **kwargs)

    def bind_tools(self, tools: list, **kwargs) -> "LLMRouter":
        return LLMRouter(
            models=list(self._models),
            bound_tools=list(tools),
            service_factory=self._service_factory,
            max_retries=self._max_retries,
            base_delay=self._base_delay,
            backoff_multiplier=self._backoff_multiplier,
            max_delay=self._max_delay,
            provider=self._provider,
        )
