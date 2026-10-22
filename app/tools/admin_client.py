"""Admin integration: asks the external CLM assistant in Unique and returns its answer as agent evidence.

The answer is never posted to the RM chat; it flows to the sufficiency check and the final agent like any other
agent result.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

from app.components.react.models import SourceItem, ToolOutput
from app.config import settings
from app.constants import SOURCE_REF_TOKEN
from app.services.unique_sdk_client import configure_unique_sdk
from app.utils.logger import logger


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read a field from an SDK response that may be a dict or an object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class AdminSubagent:
    """One question to the CLM assistant space."""

    def __init__(
        self,
        *,
        user_id: str,
        company_id: str,
        query: str,
        clm_assistant_id: str | None = None,
        clm_chat_id: str | None = None,
    ) -> None:
        self.user_id = user_id
        self.company_id = company_id
        self.query = query
        self.clm_assistant_id = clm_assistant_id or settings.ADMIN_CLM_ASSISTANT_ID
        # No chat id: the SDK opens a new CLM chat for this query, so no earlier question shapes the answer.
        self.clm_chat_id = clm_chat_id or (settings.ADMIN_CLM_CHAT_ID if settings.ADMIN_CLM_REUSE_CHAT else None)

        required = {
            "user_id": self.user_id,
            "company_id": self.company_id,
            "clm_assistant_id": self.clm_assistant_id,
            "query": self.query,
        }
        if settings.ADMIN_CLM_REUSE_CHAT:
            required["clm_chat_id"] = self.clm_chat_id
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"Missing required admin agent value(s): {', '.join(missing)}")

        configure_unique_sdk()

    async def send_query(self) -> Any:
        """Send the query to the CLM assistant and wait until its answer is complete."""
        from unique_sdk.utils.chat_in_space import send_message_and_wait_for_completion

        optional: dict[str, Any] = {}
        stop_condition = settings.ADMIN_CLM_STOP_CONDITION.strip()
        # Older SDK releases have no stop_condition and always wait on stoppedStreamingAt.
        if stop_condition and "stop_condition" in inspect.signature(send_message_and_wait_for_completion).parameters:
            optional["stop_condition"] = stop_condition
        response = await send_message_and_wait_for_completion(
            user_id=self.user_id,
            company_id=self.company_id,
            assistant_id=self.clm_assistant_id,
            chat_id=self.clm_chat_id,
            text=self.query,
            max_wait=settings.ADMIN_AGENT_TIMEOUT_SECONDS,
            **optional,
        )
        logger.info(
            "[ADMIN] clm_answered chat_id=%s stop_condition=%s chars=%s references=%s inline_markers=%s",
            _get(response, "chatId") or self.clm_chat_id or "-", optional.get("stop_condition", "stoppedStreamingAt"),
            len(str(_get(response, "text") or "")), len(_get(response, "references") or []),
            len(_SUP_MARKER.findall(str(_get(response, "text") or ""))),
        )
        return response


def _source_id(ref: Any) -> str:
    return str(_get(ref, "sourceId") or _get(ref, "url") or _get(ref, "name") or "")


_SUP_MARKER = re.compile(r"<sup>\s*(\d+)\s*</sup>")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([.,;:!?])")


def _citing_excerpt(text: str, sequence_number: Any, *, max_chars: int = 400) -> str:
    """The answer sentences that cite one reference, used as its chunk text when CLM gives no description."""
    hits = [
        _SPACE_BEFORE_PUNCTUATION.sub(r"\1", " ".join(_SUP_MARKER.sub("", sentence).split()))
        for sentence in _SENTENCE_BREAK.split(text)
        if str(sequence_number) in _SUP_MARKER.findall(sentence)
    ]
    excerpt = " ".join(hit for hit in hits if hit)
    return excerpt if len(excerpt) <= max_chars else excerpt[: max_chars - 3].rstrip() + "..."


def _sources(text: str, references: list[Any]) -> list[SourceItem]:
    """One static source chunk per CLM reference: its name, hyperlink and the text it backs."""
    return [
        SourceItem(
            source_id=_source_id(ref),
            title=str(_get(ref, "name") or ""),
            uri=str(_get(ref, "url") or ""),
            snippet=str(_get(ref, "description") or "") or _citing_excerpt(text, _get(ref, "sequenceNumber")),
            source=str(_get(ref, "source") or ""),
        )
        for ref in references
        if _source_id(ref)
    ]


def _link_citations(text: str, references: list[Any]) -> str:
    """Replace the CLM answer's <sup>k</sup> markers with tokens the output reducer turns into [sourceN]."""
    by_sequence: dict[int, str] = {}
    for ref in references:
        try:  # the API may return the number as an int or a string
            by_sequence.setdefault(int(_get(ref, "sequenceNumber")), _source_id(ref))
        except (TypeError, ValueError):
            continue

    def replace(match: re.Match[str]) -> str:
        source_id = by_sequence.get(int(match.group(1)))
        return SOURCE_REF_TOKEN.format(source_id=source_id) if source_id else ""

    return _SUP_MARKER.sub(replace, text)


class UniqueAdminAgentClient:
    """Live admin client: forwards the query to the CLM assistant and returns its cited answer as tool output."""

    async def ask(self, query: str, metadata: dict[str, Any]) -> ToolOutput:
        subagent = AdminSubagent(
            user_id=metadata.get("user_id") or settings.UNIQUE_USER_ID,
            company_id=metadata.get("company_id") or settings.UNIQUE_COMPANY_ID,
            query=query,
        )
        response = await subagent.send_query()
        text = str(_get(response, "text") or "").strip()
        if not text:
            raise ValueError("CLM assistant returned an empty answer")
        references = list(_get(response, "references") or [])
        return ToolOutput(content=_link_citations(text, references), sources=_sources(text, references))
