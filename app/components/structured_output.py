"""Provider-agnostic structured LLM output: JSON in the prompt, Pydantic validation, repair retries.

Usage:
    decision = await invoke_structured(llm, messages, MySchema, label="router", post_validate=check)
`post_validate` may raise ValueError to reject an otherwise valid object; the message is fed back to the model.

How a model reply is handled:
1. Output that is not parseable JSON: one pass of LangChain's OutputFixingParser (langchain-classic), which
   shows the model the schema, its broken output and the error.
2. Schema or post_validate rejections: a reprompt inside the original conversation, so the model fixes the
   content with full context. The fixer is not used here: without the conversation it would invent content.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, TypeVar

from langchain_classic.output_parsers.fix import OutputFixingParser
from langchain_classic.output_parsers.prompts import NAIVE_FIX_PROMPT
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.output_parsers import BaseOutputParser, StrOutputParser
from langchain_core.prompt_values import PromptValue
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

from app.components.llm_call_context import llm_call
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


class JsonObjectParser(BaseOutputParser[dict]):
    """Parser handed to OutputFixingParser: extracts the JSON object, with the target schema as its instructions."""

    format_instructions: str

    def parse(self, text: str) -> dict[str, Any]:
        try:
            return extract_json_object(text)
        except ValueError as exc:
            raise OutputParserException(f"Output is not a valid JSON object: {exc}", llm_output=text) from exc

    def get_format_instructions(self) -> str:
        return self.format_instructions

    @property
    def _type(self) -> str:
        return "json_object"


async def fix_json_with_llm(llm: Any, schema: type[BaseModel], text: str, *, label: str) -> dict[str, Any] | None:
    """One OutputFixingParser pass over output that is not parseable JSON; None when it cannot help."""

    async def _call_llm(prompt_value: PromptValue) -> AIMessage:
        # The project's LLM (router or test double) is not a LangChain Runnable, so it is adapted here.
        return await llm.ainvoke(prompt_value.to_messages())

    fixer = OutputFixingParser(
        parser=JsonObjectParser(format_instructions=schema_instructions(schema)),
        retry_chain=NAIVE_FIX_PROMPT | RunnableLambda(_call_llm) | StrOutputParser(),
        max_retries=1,
        legacy=False,
    )
    try:
        with llm_call("output_fixer"):
            payload = await fixer.aparse(text)
    except Exception as exc:  # best effort: the contextual reprompt still follows
        log.warning("[STRUCTURED] fixer_failed label=%s error=%s", label, preview(str(exc)))
        return None
    return payload


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
    fixer_used = False

    for attempt in range(1, max(1, max_attempts) + 1):
        text = extract_text((await llm.ainvoke(conversation)).content)
        fixed = False
        try:
            try:
                payload = extract_json_object(text)
            except ValueError:
                if fixer_used:  # at most one fixer call per request, to bound the extra LLM cost
                    raise
                fixer_used = True
                payload = await fix_json_with_llm(llm, schema, text, label=label)
                if payload is None:
                    raise
                fixed = True
            result = schema.model_validate(payload)
            result = post_validate(result) if post_validate else result
            # A repaired or retried success is worth seeing at info; a clean first-attempt success is routine.
            (log.info if fixed or attempt > 1 else log.debug)(
                "[STRUCTURED] ok label=%s attempt=%s/%s fixed_by_output_fixer=%s result=%s",
                label, attempt, max_attempts, fixed, preview(result),
            )
            return result
        except ValueError as exc:
            last_error = exc
            log.warning(
                "[STRUCTURED] rejected label=%s attempt=%s/%s error=%s raw=%s",
                label, attempt, max_attempts, preview(str(exc)), preview(text),
            )
            conversation = [
                *conversation,
                AIMessage(content=text),
                HumanMessage(content=f"That output was rejected: {exc}\nReturn only the corrected JSON object."),
            ]

    log.error("[STRUCTURED] gave_up label=%s attempts=%s last_error=%s", label, max_attempts, preview(str(last_error)))
    raise StructuredOutputError(f"{label}: no valid output after {max_attempts} attempts: {last_error}")
