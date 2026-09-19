from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional

from langchain_core.messages import BaseMessage

from app.config import settings


def _safe_set_attributes(span: Any, attributes: dict[str, Any]) -> None:
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            span.set_attribute(key, value)
        else:
            span.set_attribute(key, str(value))


def _message_preview(messages: list[BaseMessage], max_len: int = 400) -> str:
    if not messages:
        return ""
    first = messages[-1]
    content = getattr(first, "content", "")
    text = content if isinstance(content, str) else str(content)
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _extract_usage(payload: Any) -> Optional[dict[str, int]]:
    if payload is None:
        return None

    usage = getattr(payload, "usage_metadata", None)
    if isinstance(usage, dict):
        prompt_tokens = usage.get("input_tokens") or usage.get("prompt_tokens")
        completion_tokens = usage.get("output_tokens") or usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        if total_tokens is None and isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
            total_tokens = prompt_tokens + completion_tokens
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    response_metadata = getattr(payload, "response_metadata", None)
    if isinstance(response_metadata, dict):
        token_usage = response_metadata.get("token_usage") or response_metadata.get("usage")
        if isinstance(token_usage, dict):
            prompt_tokens = token_usage.get("prompt_tokens") or token_usage.get("input_tokens")
            completion_tokens = token_usage.get("completion_tokens") or token_usage.get("output_tokens")
            total_tokens = token_usage.get("total_tokens")
            if total_tokens is None and isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
                total_tokens = prompt_tokens + completion_tokens
            return {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            }

    if isinstance(payload, dict):
        usage_dict = payload.get("usage")
        if isinstance(usage_dict, dict):
            return {
                "prompt_tokens": usage_dict.get("prompt_tokens") or usage_dict.get("input_tokens"),
                "completion_tokens": usage_dict.get("completion_tokens") or usage_dict.get("output_tokens"),
                "total_tokens": usage_dict.get("total_tokens"),
            }
    return None


def _get_tracer() -> Any:
    from app.services import observability as obs

    return getattr(obs, "_tracer", None)


def _get_provider() -> Any:
    from app.services import observability as obs

    return getattr(obs, "_provider", None)


@contextmanager
def operation_span(
    span_name: str,
    *,
    kind: str,
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[Optional[Any]]:
    tracer = _get_tracer()
    if tracer is None:
        yield None
        return

    with tracer.start_as_current_span(span_name) as span:
        attrs = {"openinference.span.kind": kind}
        if attributes:
            attrs.update(attributes)
        _safe_set_attributes(span, attrs)
        yield span


@contextmanager
def llm_span(
    span_name: str,
    *,
    model: str,
    provider: str,
    messages: list[BaseMessage],
    metadata: Optional[dict[str, Any]] = None,
) -> Iterator[Optional[Any]]:
    tracer = _get_tracer()
    if tracer is None:
        yield None
        return

    with tracer.start_as_current_span(span_name) as span:
        attrs = {
            "openinference.span.kind": "LLM",
            "llm.model_name": model,
            "llm.provider": provider,
            "llm.message_count": len(messages),
        }
        if settings.PHOENIX_CAPTURE_MESSAGE_CONTENT:
            attrs["input.value"] = _message_preview(messages)
        if metadata:
            for key, value in metadata.items():
                attrs[f"app.{key}"] = value
        _safe_set_attributes(span, attrs)
        yield span


def record_llm_output(span: Any, output: str, usage: Optional[dict[str, int]] = None) -> None:
    if span is None:
        return
    attrs: dict[str, Any] = {}
    if settings.PHOENIX_CAPTURE_MESSAGE_CONTENT:
        attrs["output.value"] = output
    if usage:
        attrs["llm.token_count.prompt"] = usage.get("prompt_tokens")
        attrs["llm.token_count.completion"] = usage.get("completion_tokens")
        attrs["llm.token_count.total"] = usage.get("total_tokens")
    _safe_set_attributes(span, attrs)


def record_llm_result(span: Any, result: Any) -> None:
    if span is None or result is None:
        return

    content = getattr(result, "content", "")
    if isinstance(content, list):
        output = "".join(str(item) for item in content)
    else:
        output = content if isinstance(content, str) else str(content or "")
    record_llm_output(span, output, _extract_usage(result))


def record_exception(span: Any, exc: Exception) -> None:
    if span is None:
        return
    try:
        span.record_exception(exc)
        status = getattr(type(span), "Status", None)
        status_code = getattr(type(span), "StatusCode", None)
        if status is None or status_code is None:
            try:
                from opentelemetry.trace import Status, StatusCode  # type: ignore[reportMissingImports]

                status = Status
                status_code = StatusCode
            except Exception:
                status = None
                status_code = None
        if status is not None and status_code is not None:
            span.set_status(status(status_code.ERROR, str(exc)))
        span.set_attribute("error.type", type(exc).__name__)
        span.set_attribute("error.message", str(exc))
        provider = _get_provider()
        if provider is not None:
            provider.force_flush(timeout_millis=1000)
    except Exception:
        return