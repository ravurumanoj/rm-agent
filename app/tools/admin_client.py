"""Admin integration: asks the external CLM assistant in Unique and can post its answer to the RM chat."""

from __future__ import annotations

import re
from typing import Any

from app.components.react.models import SourceItem, ToolOutput
from app.config import settings
from app.constants import SOURCE_REF_TOKEN
from app.services.unique_chat import now_iso
from app.services.unique_sdk_client import configure_unique_sdk
from app.utils.logger import logger


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read a field from an SDK response that may be a dict or an object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class AdminSubagent:
    """One question to the CLM assistant space; the RM chat ids are only needed to post the answer back."""

    def __init__(
        self,
        *,
        user_id: str,
        company_id: str,
        rm_assistant_id: str,
        rm_chat_id: str,
        query: str,
        clm_assistant_id: str | None = None,
        clm_chat_id: str | None = None,
    ) -> None:
        self.user_id = user_id
        self.company_id = company_id
        self.rm_assistant_id = rm_assistant_id
        self.rm_chat_id = rm_chat_id
        self.query = query
        self.clm_assistant_id = clm_assistant_id or settings.ADMIN_CLM_ASSISTANT_ID
        self.clm_chat_id = clm_chat_id or settings.ADMIN_CLM_CHAT_ID

        required = {
            "user_id": self.user_id,
            "company_id": self.company_id,
            "clm_assistant_id": self.clm_assistant_id,
            "clm_chat_id": self.clm_chat_id,
            "query": self.query,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"Missing required admin agent value(s): {', '.join(missing)}")

        configure_unique_sdk()

    async def send_query(self) -> Any:
        """Send the query to the CLM assistant and wait until its answer is complete."""
        from unique_sdk.utils.chat_in_space import send_message_and_wait_for_completion

        try:
            response = await send_message_and_wait_for_completion(
                user_id=self.user_id,
                company_id=self.company_id,
                assistant_id=self.clm_assistant_id,
                chat_id=self.clm_chat_id,
                text=self.query,
                max_wait=settings.ADMIN_AGENT_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.exception("[ADMIN] CLM assistant call failed")
            raise
        logger.info("[ADMIN] CLM assistant answered")
        return response

    async def send_response(self, response: Any) -> Any:
        """Post the CLM answer (with references) as an assistant message in the RM chat."""
        import unique_sdk

        text = _get(response, "text")
        if not text:
            raise ValueError("CLM assistant response does not contain text")

        now = now_iso()
        try:
            message = await unique_sdk.Message.create_async(
                user_id=self.user_id,
                company_id=self.company_id,
                assistantId=self.rm_assistant_id,
                chatId=self.rm_chat_id,
                text=text,
                references=_get(response, "references") or [],
                role="ASSISTANT",
                completedAt=now,
            )
            return await unique_sdk.Message.modify_async(
                user_id=self.user_id,
                company_id=self.company_id,
                id=_get(message, "id"),
                chatId=_get(message, "chatId"),
                completedAt=now,
                stoppedStreamingAt=now,
            )
        except Exception:
            logger.exception("[ADMIN] posting the answer to the RM chat failed")
            raise


def _source_id(ref: Any) -> str:
    return str(_get(ref, "sourceId") or _get(ref, "url") or _get(ref, "name") or "")


def _sources(references: list[Any]) -> list[SourceItem]:
    return [
        SourceItem(
            source_id=_source_id(ref),
            title=str(_get(ref, "name") or ""),
            uri=str(_get(ref, "url") or ""),
            snippet=str(_get(ref, "description") or ""),
            source=str(_get(ref, "source") or ""),
        )
        for ref in references
        if _source_id(ref)
    ]


def _link_citations(text: str, references: list[Any]) -> str:
    """Replace the CLM answer's <sup>k</sup> markers with tokens the output reducer turns into [sourceN]."""
    by_sequence = {_get(ref, "sequenceNumber"): _source_id(ref) for ref in references}

    def replace(match: re.Match[str]) -> str:
        source_id = by_sequence.get(int(match.group(1)))
        return SOURCE_REF_TOKEN.format(source_id=source_id) if source_id else ""

    return re.sub(r"<sup>\s*(\d+)\s*</sup>", replace, text)


class UniqueAdminAgentClient:
    """Live admin client: forwards the query to the CLM assistant; ids come from the webhook metadata."""

    async def ask(self, query: str, metadata: dict[str, Any]) -> ToolOutput:
        subagent = AdminSubagent(
            user_id=metadata.get("user_id") or settings.UNIQUE_USER_ID,
            company_id=metadata.get("company_id") or settings.UNIQUE_COMPANY_ID,
            rm_assistant_id=metadata.get("rm_assistant_id", ""),
            rm_chat_id=metadata.get("rm_chat_id", ""),
            query=query,
        )
        response = await subagent.send_query()
        text = str(_get(response, "text") or "").strip()
        if not text:
            raise ValueError("CLM assistant returned an empty answer")
        references = list(_get(response, "references") or [])
        return ToolOutput(content=_link_citations(text, references), sources=_sources(references))
