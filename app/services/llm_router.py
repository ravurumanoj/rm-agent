from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial
from typing import Any, AsyncIterator, Callable, Iterator, Optional, Sequence

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage

from app.components.messages import extract_text
from app.components.resilience import RetryStats, classify_error
from app.config import settings
from app.services.llm_core import (
    ProviderUnavailableError,
    active_provider,
    build_model_chain,
    configure_proxy_env,
    create_service_for_model,
    invoke_with_retry,
    parse_fallback_models,
    provider_for_model,
    resolve_primary_model,
)
from app.services.observability import configure_observability
from app.components.llm_call_context import current_llm_call_name
from app.observability import (
    Attr,
    normalize_token_usage,
    record_failure,
    record_llm_response,
    record_retry,
    set_attributes,
    start_llm_span,
)
from app.utils.logger import logger, preview


def token_usage(result: Any) -> dict[str, int] | None:
    """Provider-reported usage from a LangChain message (usage_metadata or response_metadata), if any."""
    metadata = getattr(result, "response_metadata", None) or {}
    for candidate in (getattr(result, "usage_metadata", None), metadata.get("token_usage"), metadata.get("usage")):
        usage = normalize_token_usage(candidate)
        if usage:
            return usage
    return None


def _fallback_note(next_model: str) -> str:
    return f"falling back to {next_model}" if next_model else "no fallback model left"


def _record_llm_failure(span: Any, exc: Exception, *, model: str, provider: str, then: str, attempts: int = 0) -> None:
    """Why a model call failed and what the router does next, on its LLM span (which also names model and provider)."""
    record_failure(
        span,
        exc,
        where=f"LLM call to {model} ({provider}) failed; {then}",
        kind=classify_error(exc).value,
        attributes={Attr.RETRY_ATTEMPTS: attempts or None},
    )


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
    """Route LLM calls across a model chain with fallback; each model goes to the provider that serves it."""

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
        pin_provider: bool = False,
    ) -> None:
        self._models = list(models)
        self._bound_tools = list(bound_tools or [])
        self._cache: dict[str, Any] = {}
        # Preferred provider; with pin_provider every model goes to it without a lookup.
        self._provider = (provider or settings.LLM_PROVIDER).strip().lower()
        self._pin_provider = pin_provider
        self._model_providers: dict[str, str] = {}
        self._service_factory = service_factory
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

    def _provider_for(self, model: str) -> str:
        if self._pin_provider:
            return self._provider
        if model not in self._model_providers:
            self._model_providers[model] = provider_for_model(model, self._provider)
        return self._model_providers[model]

    def _svc(self, model: str):
        if model not in self._cache:
            tools = self._bound_tools or None
            if self._service_factory:
                self._cache[model] = self._service_factory(model, tools)
            else:
                self._cache[model] = create_service_for_model(model, tools, provider=self._provider_for(model))
        return self._cache[model]

    async def _invoke_one(self, model: str, messages: list[BaseMessage], *, next_model: str = "", **kwargs) -> AIMessage:
        provider = self._provider_for(model)
        started = time.perf_counter()
        stats = RetryStats(attempts=0)
        # One LLM span per call (provider clients add details to it). Each retried attempt is an event on it; a
        # failure records the model, the error kind and what the router does next.
        with start_llm_span(
            caller=current_llm_call_name(),
            model=model,
            provider=provider,
            messages=messages,
            attributes={"app.llm.max_retries": self._max_retries, "app.llm.fallback_model": model != self._chain()[0]},
        ) as span:
            try:
                svc = self._svc(model)  # inside the span, so a client that cannot be built is a traced failure too
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
                        stats=stats,
                        on_retry=partial(record_retry, span),
                    )
            except Exception as exc:
                _record_llm_failure(
                    span, exc, model=model, provider=provider, then=_fallback_note(next_model), attempts=stats.attempts
                )
                raise
            tool_calls = getattr(result, "tool_calls", None) or []
            usage = token_usage(result)
            record_llm_response(span, output=result.content, usage=usage, messages=messages, tool_calls=tool_calls)
            set_attributes(span, {"app.llm.tool_call_count": len(tool_calls), Attr.RETRY_ATTEMPTS: stats.attempts or None})
            logger.info(
                "[LLM] call_completed call=%s model=%s duration_ms=%s messages=%s tool_calls=%s tokens=%s output=%s",
                current_llm_call_name() or "-", model, int((time.perf_counter() - started) * 1000), len(messages),
                len(tool_calls), usage or "not reported", preview(extract_text(result.content)),
            )
            return result

    async def ainvoke(self, messages: list[BaseMessage], **kwargs) -> AIMessage:
        last_exc: Exception | None = None
        models = self._chain()
        for index, model in enumerate(models):
            next_model = models[index + 1] if index + 1 < len(models) else ""
            try:
                result = await self._invoke_one(model, messages, next_model=next_model, **kwargs)
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
        for index, model in enumerate(models):
            yielded_any = False
            provider = self._provider_for(model)
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
                    try:
                        async for chunk in self._svc(model).astream(messages, **kwargs):
                            content = getattr(chunk, "content", "")
                            if content and not collected_chunks:
                                ttft_ms = int((time.perf_counter() - started) * 1000)
                                set_attributes(span, {"app.llm.time_to_first_token_ms": ttft_ms})
                            if content:
                                collected_chunks.append(content if isinstance(content, str) else str(content))
                            yielded_any = True
                            yield chunk
                    except Exception as exc:
                        then = (
                            "the stream broke after output was sent, so no fallback"
                            if yielded_any
                            else _fallback_note(models[index + 1] if index + 1 < len(models) else "")
                        )
                        _record_llm_failure(span, exc, model=model, provider=provider, then=then)
                        raise
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
            pin_provider=self._pin_provider,
        )


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
    """Build a lazy LLM router; omitted arguments come from settings.

    Without `provider`, each model goes to the provider that lists it (LLM_PROVIDER first); an explicit
    `provider` sends every model to that provider.
    """
    configure_proxy_env()
    configure_observability()

    pin_provider = provider is not None
    provider = active_provider(provider)
    model_chain = build_model_chain(resolve_primary_model(provider, model), parse_fallback_models(fallback_models))
    logger.info("[LLM] router_ready provider=%s primary=%s fallbacks=%s", provider, model_chain[0], model_chain[1:])

    return LLMRouter(
        models=model_chain,
        provider=provider,
        pin_provider=pin_provider,
        bound_tools=bound_tools,
        max_retries=max_retries,
        base_delay=base_delay,
        backoff_multiplier=backoff_multiplier,
        max_delay=max_delay,
    )
