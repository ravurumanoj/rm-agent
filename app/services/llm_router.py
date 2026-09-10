from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable, Optional

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage

from app.config import settings
from app.services.llm_core import (
    ProviderUnavailableError,
    create_service_for_model,
    detect_provider,
    invoke_with_retry,
)
from app.services.observability import llm_span
from app.utils.logger import logger


class LLMRouter:
    """Route LLM calls across model/provider chain with fallback."""

    def __init__(
        self,
        models: list[str],
        bound_tools: Optional[list] = None,
        service_factory: Callable = create_service_for_model,
        max_retries: Optional[int] = None,
        base_delay: Optional[float] = None,
        backoff_multiplier: Optional[float] = None,
        max_delay: Optional[float] = None,
    ) -> None:
        self._models = list(models)
        self._bound_tools = list(bound_tools or [])
        self._cache: dict[str, Any] = {}
        self._service_factory = service_factory
        self._max_retries = settings.LLM_MAX_RETRIES if max_retries is None else max_retries
        self._base_delay = settings.LLM_RETRY_BASE_DELAY if base_delay is None else base_delay
        self._backoff_multiplier = (
            settings.LLM_RETRY_BACKOFF_MULTIPLIER if backoff_multiplier is None else backoff_multiplier
        )
        self._max_delay = settings.LLM_RETRY_MAX_DELAY if max_delay is None else max_delay

    def _svc(self, model: str):
        if model not in self._cache:
            self._cache[model] = self._service_factory(model, self._bound_tools or None)
        return self._cache[model]

    async def _invoke_one(self, model: str, messages: list[BaseMessage], **kwargs) -> AIMessage:
        svc = self._svc(model)
        provider = detect_provider(model)
        with llm_span(
            "llm.ainvoke",
            model=model,
            provider=provider,
            messages=messages,
            metadata={"max_retries": self._max_retries},
        ) as span:
            try:
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
                if span is not None:
                    span.set_attribute("llm.success", True)
                return result
            except Exception:
                if span is not None:
                    span.set_attribute("llm.success", False)
                raise

    async def ainvoke(self, messages: list[BaseMessage], **kwargs) -> AIMessage:
        last_exc: Exception | None = None
        for model in self._models:
            try:
                result = await self._invoke_one(model, messages, **kwargs)
                if model != self._models[0]:
                    logger.info("LLMRouter fallback used model '%s'", model)
                return result
            except ProviderUnavailableError as exc:
                logger.warning("Provider for model '%s' unavailable: %s", model, exc)
                last_exc = exc
            except Exception as exc:
                logger.warning("Model '%s' failed after retries: %s", model, exc)
                last_exc = exc
        raise last_exc or RuntimeError("All configured LLM models failed")

    async def astream(self, messages: list[BaseMessage], **kwargs) -> AsyncIterator[AIMessageChunk]:
        last_exc: Exception | None = None
        for model in self._models:
            yielded_any = False
            provider = detect_provider(model)
            try:
                with llm_span(
                    "llm.astream",
                    model=model,
                    provider=provider,
                    messages=messages,
                    metadata={"stream": True},
                ) as span:
                    try:
                        async for chunk in self._svc(model).astream(messages, **kwargs):
                            yielded_any = True
                            yield chunk
                        if span is not None:
                            span.set_attribute("llm.success", True)
                    except Exception:
                        if span is not None:
                            span.set_attribute("llm.success", False)
                        raise
                if model != self._models[0]:
                    logger.info("LLMRouter stream fallback used model '%s'", model)
                return
            except ProviderUnavailableError as exc:
                logger.warning("Provider for model '%s' unavailable: %s", model, exc)
                last_exc = exc
            except Exception as exc:
                if yielded_any:
                    raise
                logger.warning("Model '%s' failed before stream output: %s", model, exc)
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
        svc = self._svc(self._models[0])
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
        )
