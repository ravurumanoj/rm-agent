from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any, AsyncIterator

from app.schemas.citations import SSEEvent
from app.utils.logger import logger


class SSEListenerService:
    """In-memory pub/sub bus for local webhook event streaming."""

    def __init__(self, *, max_events: int = 500) -> None:
        self._events: deque[SSEEvent] = deque(maxlen=max_events)
        self._subscribers: list[tuple[str, asyncio.Queue[SSEEvent]]] = []
        self._lock = asyncio.Lock()
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return str(self._counter)

    async def publish(self, *, event: str, data: dict[str, Any], chat_id: str = "") -> None:
        item = SSEEvent(
            id=self._next_id(),
            event=event,
            data=data,
            chat_id=chat_id,
            timestamp=time.time(),
        )
        async with self._lock:
            self._events.append(item)
            logger.debug("[SSE] publish event=%s chat_id=%s subscribers=%s", event, chat_id, len(self._subscribers))
            for filter_chat_id, queue in list(self._subscribers):
                if filter_chat_id and filter_chat_id != chat_id:
                    continue
                if queue.full():
                    try:
                        queue.get_nowait()
                    except Exception:
                        pass
                try:
                    queue.put_nowait(item)
                except Exception:
                    continue

    async def subscribe(self, *, chat_id: str = "", replay_last: int = 20) -> AsyncIterator[SSEEvent]:
        queue: asyncio.Queue[SSEEvent] = asyncio.Queue(maxsize=100)

        async with self._lock:
            self._subscribers.append((chat_id, queue))
            logger.info("[SSE] subscriber_added chat_filter=%s total=%s", chat_id, len(self._subscribers))
            if replay_last > 0:
                replay_items = list(self._events)[-replay_last:]
                for item in replay_items:
                    if chat_id and item.chat_id != chat_id:
                        continue
                    try:
                        queue.put_nowait(item)
                    except Exception:
                        break

        try:
            while True:
                item = await queue.get()
                yield item
        finally:
            async with self._lock:
                self._subscribers = [s for s in self._subscribers if s[1] is not queue]
                logger.info("[SSE] subscriber_removed chat_filter=%s total=%s", chat_id, len(self._subscribers))

    async def list_recent(self, *, chat_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 500))
        async with self._lock:
            items = list(self._events)
        if chat_id:
            items = [item for item in items if item.chat_id == chat_id]
        items = items[-safe_limit:]
        return [
            {
                "id": item.id,
                "event": item.event,
                "chat_id": item.chat_id,
                "timestamp": item.timestamp,
                "data": item.data,
            }
            for item in items
        ]


sse_listener = SSEListenerService()


def format_sse_message(item: SSEEvent) -> str:
    payload = json.dumps(item.data, ensure_ascii=True)
    lines = [
        f"id: {item.id}",
        f"event: {item.event}",
        f"data: {payload}",
        "",
    ]
    return "\n".join(lines)
