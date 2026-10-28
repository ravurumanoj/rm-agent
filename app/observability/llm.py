"""One LLM span per model call, with what Arize AX needs for cost: model, provider and token counts.

    with start_llm_span(caller="orchestrator.routing", model=model, provider="openai", messages=messages) as span:
        reply = await client.ainvoke(messages)
        record_llm_response(span, output=reply.content, usage=normalize_token_usage(reply.usage), messages=messages)

Arize AX prices a span by matching llm.model_name (and llm.provider, if the cost config sets one) and multiplying
llm.token_count.prompt / .completion. When a provider returns no usage, counts are estimated and flagged with
app.token_count.estimated so real and estimated costs can be told apart.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Iterator, Mapping, Sequence

from openinference.instrumentation import get_llm_attributes, get_llm_token_count_attributes
from opentelemetry.trace import Span

from app.observability.conventions import Attr, SpanKind, SpanName
from app.observability.setup import get_config
from app.observability.spans import capture_content, record_output, set_attributes, start_span

# Message.type of LangChain messages -> OpenInference role
_ROLE_BY_TYPE = {"system": "system", "human": "user", "ai": "assistant", "tool": "tool"}
# Usage key spellings seen across providers (OpenAI snake_case, Unique/Azure camelCase, LangChain usage_metadata)
# (plus this module's own "prompt" / "completion" / "total", so normalising twice is harmless)
_PROMPT_KEYS = ("prompt_tokens", "input_tokens", "promptTokens", "inputTokens", "prompt")
_COMPLETION_KEYS = ("completion_tokens", "output_tokens", "completionTokens", "outputTokens", "completion")
_TOTAL_KEYS = ("total_tokens", "totalTokens", "total")


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(item.get("text", "") if isinstance(item, dict) else str(item) for item in content)
    return str(content or "")


def to_openinference_messages(messages: Sequence[Any]) -> list[dict[str, Any]]:
    """LangChain messages (or {"role", "content"} dicts) as OpenInference messages, with the tool calls a reply
    asked for and the tool call id a tool result answers."""
    converted: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, Mapping):
            converted.append({"role": str(message.get("role", "user")), "content": message_text(message.get("content"))})
            continue
        item: dict[str, Any] = {
            "role": _ROLE_BY_TYPE.get(getattr(message, "type", ""), "user"),
            "content": message_text(getattr(message, "content", "")),
        }
        if getattr(message, "tool_calls", None):
            item["tool_calls"] = to_openinference_tool_calls(message.tool_calls)
        if getattr(message, "tool_call_id", None):
            item["tool_call_id"] = str(message.tool_call_id)
        converted.append(item)
    return converted


def to_openinference_tool_calls(tool_calls: Sequence[Any]) -> list[dict[str, Any]]:
    """LangChain tool calls ({"id", "name", "args"}) in the OpenInference shape."""
    return [
        {"id": call.get("id"), "function": {"name": call.get("name"), "arguments": call.get("args") or {}}}
        for call in tool_calls
        if isinstance(call, Mapping)
    ]


def normalize_token_usage(usage: Any) -> dict[str, int] | None:
    """A provider usage block in any common spelling -> {"prompt", "completion", "total"}; None if absent."""
    if not isinstance(usage, Mapping):
        return None

    def first(keys: tuple[str, ...]) -> int | None:
        return next((usage[key] for key in keys if isinstance(usage.get(key), int)), None)

    prompt, completion, total = first(_PROMPT_KEYS), first(_COMPLETION_KEYS), first(_TOTAL_KEYS)
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    counts = {"prompt": prompt, "completion": completion, "total": total}
    counts = {key: value for key, value in counts.items() if value is not None}
    return counts or None


def estimate_token_usage(messages: Sequence[Any], output: str) -> dict[str, int]:
    """Approximate counts using the OpenAI chat format overhead (about 4 tokens per message, 2 for the reply)."""
    prompt = sum(_count_tokens(message_text(_content(message))) + 4 for message in messages) + 2
    completion = _count_tokens(output)
    return {"prompt": prompt, "completion": completion, "total": prompt + completion}


@contextmanager
def start_llm_span(
    *,
    caller: str,
    model: str,
    provider: str,
    messages: Sequence[Any],
    invocation_parameters: Mapping[str, Any] | None = None,
    attributes: Mapping[str, Any] | None = None,
) -> Iterator[Span]:
    """The LLM span for one model call, named llm.<caller> (e.g. llm.portfolio_agent.step_2)."""
    caller = caller or "call"
    llm_attributes = get_llm_attributes(
        provider=get_config().llm_provider_label or provider,
        model_name=model,
        invocation_parameters={"model": model, **(invocation_parameters or {})},
        input_messages=to_openinference_messages(messages) if capture_content() else None,
    )
    with start_span(
        SpanName.llm(caller),
        kind=SpanKind.LLM,
        attributes={
            **llm_attributes,
            Attr.LLM_CALL: caller,
            Attr.GRAPH_NODE: caller.split(".", 1)[0],
            Attr.METADATA: {"llm_call": caller, "graph_node": caller.split(".", 1)[0]},
            **(attributes or {}),
        },
        input_value=message_text(_content(messages[-1])) if messages else None,
    ) as span:
        yield span


def record_llm_response(
    span: Span,
    *,
    output: Any,
    usage: Mapping[str, int] | None = None,
    messages: Sequence[Any] | None = None,
    tool_calls: Sequence[Any] | None = None,
) -> None:
    """Response text, the tool calls it asked for, and token counts; with no usage but the prompt messages given,
    counts are estimated. A reply that only calls tools has no text, so its output is the calls."""
    text = message_text(output)
    calls = to_openinference_tool_calls(tool_calls or [])
    completion = text or (json.dumps(calls, default=str) if calls else "")
    estimated = usage is None and messages is not None
    counts = dict(usage) if usage else (estimate_token_usage(messages, completion) if estimated else None)

    record_output(span, text or ({"tool_calls": calls} if calls else text))
    if capture_content():
        reply = {"role": "assistant", "content": text, **({"tool_calls": calls} if calls else {})}
        set_attributes(span, get_llm_attributes(output_messages=[reply]))
    if counts:
        set_attributes(span, {**get_llm_token_count_attributes(counts), Attr.TOKENS_ESTIMATED: estimated})


def _content(message: Any) -> Any:
    return message.get("content") if isinstance(message, Mapping) else getattr(message, "content", "")


@lru_cache(maxsize=1)
def _tiktoken_encoder() -> Any:
    # tiktoken downloads its vocabulary on first use; without network access that would block, hence opt-in.
    if not get_config().tiktoken_estimates:
        return None
    try:
        import tiktoken

        return tiktoken.get_encoding("o200k_base")
    except Exception:
        return None


def _count_tokens(text: str) -> int:
    encoder = _tiktoken_encoder()
    if encoder is not None:
        try:
            return len(encoder.encode(text, disallowed_special=()))
        except Exception:
            pass
    return max(1, len(text) // 4) if text else 0
