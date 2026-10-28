"""Conversation history for a webhook turn, read from the Unique chat.

The latest HISTORY_RAW_TURNS turns are passed verbatim; older turns are condensed by one LLM call. The result is
the history block the orchestrator reads. Never raises: on any failure the caller falls back to the in-process
history, so a Unique or LLM outage cannot fail the turn.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import get_shared_llm
from app.components.llm_call_context import llm_call
from app.components.messages import extract_text
from app.config import settings
from app.constants import HISTORY_BLOCK_MAX_CONTENT_LEN
from app.observability import SpanKind, SpanName, record_failure, record_output, set_attributes, start_span
from app.prompts.history import HISTORY_SUMMARY_SYSTEM_PROMPT, HISTORY_SUMMARY_USER_TEMPLATE
from app.services.unique_chat import UniqueChatContext
from app.services.unique_sdk_client import configure_unique_sdk
from app.utils.logger import logger

_SUP_MARKER = re.compile(r"<sup>\s*\d+\s*</sup>")

Turn = list[dict[str, str]]


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _clean(text: str) -> str:
    """One line per message, without Unique's citation footnotes, capped like the in-process history."""
    content = " ".join(_SUP_MARKER.sub("", text).split())
    if len(content) > HISTORY_BLOCK_MAX_CONTENT_LEN:
        content = content[: HISTORY_BLOCK_MAX_CONTENT_LEN - 3] + "..."
    return content


async def fetch_chat_messages(ctx: UniqueChatContext) -> list[dict[str, str]]:
    """Earlier USER and ASSISTANT messages of the chat, oldest first, without the current turn's messages."""
    result = await configure_unique_sdk().Message.list_async(**ctx.identity, chatId=ctx.chat_id)
    current = {ctx.message_id, ctx.user_message_id} - {""}
    messages = []
    items = _get(result, "data")
    if items is None:
        items = result if isinstance(result, list) else []
    for item in items:
        role = str(_get(item, "role") or "").upper()
        text = _clean(str(_get(item, "text") or ""))
        if role not in ("USER", "ASSISTANT") or not text or _get(item, "id") in current:
            continue
        messages.append({"role": role, "text": text, "created_at": str(_get(item, "createdAt") or "")})
    if all(message["created_at"] for message in messages):
        messages.sort(key=lambda message: message["created_at"])
    return messages


def group_turns(messages: list[dict[str, str]]) -> list[Turn]:
    """A turn is one RM message plus the replies to it."""
    turns: list[Turn] = []
    for message in messages:
        if message["role"] == "USER" or not turns:
            turns.append([message])
        else:
            turns[-1].append(message)
    return turns


def _render(turns: list[Turn]) -> list[str]:
    return [
        f"  {'User' if message['role'] == 'USER' else 'Assistant'}: {message['text']}"
        for turn in turns
        for message in turn
    ]


async def summarize_turns(turns: list[Turn]) -> str:
    """Condense older turns with one LLM call; empty on failure so the recent turns are still used."""
    if not turns:
        return ""
    transcript = "\n".join(_render(turns))
    try:
        with llm_call("history_summary"):
            reply = await asyncio.wait_for(
                get_shared_llm().ainvoke(
                    [
                        SystemMessage(content=HISTORY_SUMMARY_SYSTEM_PROMPT),
                        HumanMessage(content=HISTORY_SUMMARY_USER_TEMPLATE.format(transcript=transcript)),
                    ]
                ),
                timeout=settings.HISTORY_SUMMARY_TIMEOUT_SECONDS,
            )
    except Exception:
        logger.warning("[HISTORY] summary_failed turns=%s; using recent turns only", len(turns), exc_info=True)
        return ""
    return extract_text(reply.content).strip()


async def build_unique_history_block(ctx: UniqueChatContext) -> str:
    """History block for the turn: older turns summarised, the latest turns verbatim. Empty on failure.

    Traced as step.load_history (the summary's LLM call nests under it); a fallback marks the step failed.
    """
    if not (settings.UNIQUE_HISTORY_ENABLED and ctx.chat_id):
        return ""
    with start_span(SpanName.step("load_history"), kind=SpanKind.CHAIN) as span:
        try:
            turns = group_turns(await fetch_chat_messages(ctx))
        except Exception as exc:
            logger.warning("[HISTORY] fetch_failed chat_id=%s; falling back to in-process history", ctx.chat_id, exc_info=True)
            record_failure(span, exc, where="chat history fetch failed; using the in-process history", kind="fallback")
            return ""
        set_attributes(span, {"app.history.turns": len(turns)})
        if not turns:
            return ""

        raw_count = max(1, settings.HISTORY_RAW_TURNS)
        recent, older = turns[-raw_count:], turns[:-raw_count][-max(0, settings.HISTORY_SUMMARY_MAX_TURNS):]
        summary = await summarize_turns(older) if older else ""

        sections = []
        if summary:
            sections.append(f"Summary of earlier conversation:\n{summary}")
        sections.append("Recent conversation:\n" + "\n".join(_render(recent)))
        block = "\n\n".join(sections) + "\n\n"
        (logger.info if summary or not older else logger.warning)(
            "[HISTORY] built chat_id=%s turns=%s recent=%s summarised=%s summary_ok=%s block_chars=%s",
            ctx.chat_id, len(turns), len(recent), len(older), bool(summary) if older else "-", len(block),
        )
        set_attributes(span, {"app.history.summarised_turns": len(older)})
        record_output(span, block)
        if older and not summary:
            record_failure(
                span, "no summary of the older turns", where="history summary failed; using the recent turns only",
                kind="fallback",
            )
        return block
