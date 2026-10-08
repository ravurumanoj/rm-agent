"""Everything the agent sends back to the Unique chat UI: reply with references, progress steps and live text."""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.services.citations import ReferenceManager
from app.services.unique_sdk_client import configure_unique_sdk
from app.utils.logger import logger

STEP_RUNNING = "RUNNING"
STEP_COMPLETED = "COMPLETED"
STEP_FAILED = "FAILED"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class UniqueChatContext:
    """Where in Unique a reply goes: the chat, the placeholder assistant message and the acting user."""

    chat_id: str
    assistant_id: str
    message_id: str
    user_id: str = ""
    company_id: str = ""

    @property
    def identity(self) -> dict[str, str]:
        user_id = (self.user_id or settings.UNIQUE_USER_ID or "").strip()
        company_id = (self.company_id or settings.UNIQUE_COMPANY_ID or "").strip()
        if not user_id or not company_id:
            raise RuntimeError(
                "Unique AI requires company/user identity. Set UNIQUE_COMPANY_ID and UNIQUE_USER_ID, "
                "or send userId/companyId with the event."
            )
        return {"user_id": user_id, "company_id": company_id}


def render_references(text: str, citations: list[dict[str, Any]] | None) -> tuple[str, list[dict[str, Any]]]:
    """Convert [sourceN] markers to <sup>k</sup> and build the Message.Reference list from the tracked sources."""
    if not citations:
        return text, []
    rendered, references = ReferenceManager.from_citations(citations).to_message_references(text)
    return rendered, [reference.to_unique() for reference in references]


async def write_assistant_message(
    ctx: UniqueChatContext,
    *,
    text: str,
    citations: list[dict[str, Any]] | None = None,
    started_at: str | None = None,
) -> None:
    """Write the final reply (and its references) to the assistant message."""
    text, references = render_references(text, citations)
    sdk = configure_unique_sdk()
    completed_at = now_iso()

    if ctx.message_id:
        params: dict[str, Any] = {
            "chatId": ctx.chat_id,
            "text": text,
            "stoppedStreamingAt": completed_at,
            "completedAt": completed_at,
        }
        if started_at:
            params["startedStreamingAt"] = started_at
        if references:
            params["references"] = references
        await sdk.Message.modify_async(**ctx.identity, id=ctx.message_id, **params)
        return

    # The user-message event has no placeholder message, so a new one is created; it needs the assistantId.
    if not ctx.assistant_id:
        raise RuntimeError("Cannot create an assistant message without assistantId")
    params = {"chatId": ctx.chat_id, "assistantId": ctx.assistant_id, "role": "ASSISTANT", "text": text,
              "completedAt": completed_at}
    if references:
        params["references"] = references
    await sdk.Message.create_async(**ctx.identity, **params)


async def stream_reply(ctx: UniqueChatContext, text: str, citations: list[dict[str, Any]] | None = None) -> None:
    """Push the reply to the UI in growing chunks. UI only: the final write is what persists it."""
    if not (settings.UNIQUE_STREAM_REPLY and ctx.message_id):
        return
    text, _ = render_references(text, citations)
    words = re.findall(r"\S+\s*", text)
    step = max(1, settings.UNIQUE_STREAM_CHUNK_WORDS)
    try:
        sdk = configure_unique_sdk()
        for end in range(step, len(words) + step, step):
            await sdk.Message.create_event_async(
                **ctx.identity, messageId=ctx.message_id, chatId=ctx.chat_id, text="".join(words[:end])
            )
    except Exception:
        logger.warning("[UNIQUE] live text streaming stopped", exc_info=True)


class StepLogger:
    """Shows graph progress as steps in the chat (MessageLog). Never raises: a failed log must not fail the turn."""

    def __init__(self, ctx: UniqueChatContext) -> None:
        self._ctx = ctx
        self._order = 0
        self._running: dict[str, str] = {}

    @property
    def enabled(self) -> bool:
        return settings.UNIQUE_STEPS_ENABLED and bool(self._ctx.message_id)

    async def start(self, key: str, text: str) -> None:
        if not self.enabled:
            return
        self._order += 1
        try:
            log = await configure_unique_sdk().MessageLog.create_async(
                **self._ctx.identity, messageId=self._ctx.message_id, text=text, status=STEP_RUNNING, order=self._order
            )
            self._running[key] = log.id
        except Exception:
            logger.warning("[UNIQUE] step start failed key=%s", key, exc_info=True)

    async def finish(self, key: str, *, failed: bool = False) -> None:
        log_id = self._running.pop(key, None)
        if log_id is None:
            return
        try:
            await configure_unique_sdk().MessageLog.update_async(
                **self._ctx.identity,
                message_log_id=log_id,
                status=STEP_FAILED if failed else STEP_COMPLETED,
            )
        except Exception:
            logger.warning("[UNIQUE] step finish failed key=%s", key, exc_info=True)

    async def fail_running(self) -> None:
        for key in list(self._running):
            await self.finish(key, failed=True)


# Set per turn by the webhook; graph nodes read it to report progress.
current_step_logger: ContextVar[StepLogger | None] = ContextVar("current_step_logger", default=None)
