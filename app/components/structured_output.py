"""Provider-agnostic structured LLM output: JSON in the prompt, Pydantic validation, repair retries.

Usage:
    decision = await invoke_structured(llm, messages, MySchema, label="router", post_validate=check)
`post_validate` may raise ValueError to reject an otherwise valid object; the message is fed back to the model.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, TypeVar

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel

from app.components.messages import extract_text
from app.components.text_preview import preview

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(RuntimeError):
    """Raised when the model never produced output that passes validation."""


def schema_instructions(schema: type[BaseModel]) -> str:
    """Prompt fragment telling the model to answer with JSON for the given schema."""
    return (
        "Respond with a single JSON object that validates against this JSON Schema. "
        "Output JSON only: no markdown fences, no commentary.\n"
        + json.dumps(schema.model_json_schema(), separators=(",", ":"))
    )


def extract_json_object(text: str) -> dict[str, Any]:
    """Return the first JSON object in text, tolerating code fences and surrounding prose."""
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            return value
        start = text.find("{", start + 1)
    raise ValueError("No JSON object found in model output")


async def invoke_structured(
    llm: Any,
    messages: list[BaseMessage],
    schema: type[T],
    *,
    label: str,
    max_attempts: int = 2,
    post_validate: Callable[[T], T] | None = None,
) -> T:
    """Call the LLM until its JSON validates against schema (and post_validate), up to max_attempts."""
    conversation = list(messages)
    last_error: Exception | None = None

    for attempt in range(1, max(1, max_attempts) + 1):
        text = extract_text((await llm.ainvoke(conversation)).content)
        log.debug("structured output raw label=%s attempt=%s text=%s", label, attempt, preview(text))
        try:
            result = schema.model_validate(extract_json_object(text))
            result = post_validate(result) if post_validate else result
            log.info("structured output ok label=%s attempt=%s result=%s", label, attempt, preview(result))
            return result
        except ValueError as exc: 
            last_error = exc
            log.warning("invalid structured output label=%s attempt=%s error=%s", label, attempt, preview(str(exc)))
            conversation = [
                *conversation,
                AIMessage(content=text),
                HumanMessage(content=f"That output was rejected: {exc}\nReturn only the corrected JSON object."),
            ]

    raise StructuredOutputError(f"{label}: no valid output after {max_attempts} attempts: {last_error}")
