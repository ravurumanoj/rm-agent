import asyncio
import json
import time
from typing import AsyncIterator

import httpx

from app.config import settings
from app.utils.logger import logger, preview


def _parse_sse_event(event_data: str) -> dict | None:
    """Parse a single SSE event string into a dictionary."""

    event = {}

    for line in event_data.split("\n"):
        if ":" in line:
            key, separator, value = line.partition(":")

            if separator:
                event[key.strip()] = value.lstrip()

    if "data" not in event:
        return None

    try:
        return json.loads(event["data"])

    except json.JSONDecodeError as e:
        logger.warning("[SSE] event_dropped reason=invalid_json error=%s data=%s", e, preview(event["data"]))
        return None


def _process_buffer(buffer: str) -> tuple[list[dict], str]:
    """Extract complete SSE events from the buffer."""

    events = []

    # Normalize line endings before processing.
    buffer = buffer.replace("\r\n", "\n").replace("\r", "\n")

    while "\n\n" in buffer:
        event_data, buffer = buffer.split("\n\n", 1)

        if event_data.strip():
            parsed_event = _parse_sse_event(event_data)

            if parsed_event is not None:
                events.append(parsed_event)

    return events, buffer


async def get_sse_stream() -> AsyncIterator[dict]:
    """Generate an SSE stream with automatic reconnection."""

    headers = {
        "Authorization": f"Bearer {settings.UNIQUE_APP_KEY}",
        "x-app-id": settings.UNIQUE_APP_ID,
        "x-company-id": settings.UNIQUE_COMPANY_ID,
        "Connection": "keep-alive",
        "Accept": "text/event-stream",
    }

    params = {
        "subscriptions": settings.SSE_SUBSCRIPTIONS,
    }

    while True:
        try:
            # trust_env=True picks up the proxy and CA bundle set at startup.
            async with httpx.AsyncClient(
                timeout=300.0,
                trust_env=True,
                verify=settings.SSL_VERIFY,
            ) as client:
                connection_start_time = time.monotonic()

                async with client.stream(
                    "GET",
                    settings.SSE_URL,
                    headers=headers,
                    params=params,
                ) as response:
                    # Raise an error for 4xx and 5xx responses.
                    response.raise_for_status()

                    logger.info(
                        "[SSE] connected status=%s url=%s subscriptions=%s",
                        response.status_code, settings.SSE_URL, settings.SSE_SUBSCRIPTIONS,
                    )

                    buffer = ""

                    async for chunk in response.aiter_text():
                        buffer += chunk

                        events, buffer = _process_buffer(buffer)

                        for event in events:
                            yield event

                        # Reconnect after nine minutes, before the
                        # documented ten-minute connection timeout.
                        connection_age = (
                            time.monotonic() - connection_start_time
                        )

                        if connection_age >= 540:
                            logger.info("[SSE] reconnecting age_s=%s (before the 10-minute server timeout)", int(connection_age))
                            break

            # Avoid a tight reconnect loop if the server closes immediately.
            await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("[SSE] stream_cancelled")
            raise

        except httpx.HTTPStatusError as e:
            logger.warning("[SSE] connect_rejected status=%s url=%s; retrying in 5s", e.response.status_code, settings.SSE_URL)
            await asyncio.sleep(5)

        except Exception as e:
            logger.warning("[SSE] connection_error error=%s: %s; retrying in 5s", type(e).__name__, e)
            await asyncio.sleep(5)


async def process_event(
    event_data: dict,
    webhook_url: str,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> None:
    """Validate and forward an event to the webhook."""

    async with semaphore:
        event_name, event_id = event_data.get("event", "-"), event_data.get("id", "-")
        started = time.monotonic()
        try:
            payload = event_data.get("payload")

            if not isinstance(payload, dict):
                logger.warning("[SSE] event_ignored reason=invalid_payload event=%s event_id=%s", event_name, event_id)
                return

            event_assistant_id = payload.get("assistantId")
            chat_id = payload.get("chatId") or payload.get("chatid") or "-"

            # Filter by assistant only when SSE_ASSISTANT_ID is configured.
            if (
                settings.SSE_ASSISTANT_ID
                and event_assistant_id != settings.SSE_ASSISTANT_ID
            ):
                logger.info(
                    "[SSE] event_ignored reason=other_assistant event=%s event_id=%s assistant_id=%s",
                    event_name, event_id, event_assistant_id,
                )
                return

            response = await client.post(
                webhook_url,
                json=event_data,
                headers={
                    "Content-Type": "application/json",
                },
                timeout=300.0,
            )

            duration_ms = int((time.monotonic() - started) * 1000)
            if not response.is_success:
                logger.error(
                    "[SSE] forward_failed status=%s event=%s event_id=%s chat_id=%s duration_ms=%s body=%s",
                    response.status_code, event_name, event_id, chat_id, duration_ms, preview(response.text),
                )
                return

            try:
                response_content = response.json()
            except json.JSONDecodeError:
                response_content = response.text

            handled = response_content.get("handled") if isinstance(response_content, dict) else "-"
            logger.info(
                "[SSE] forwarded event=%s event_id=%s chat_id=%s assistant_id=%s status=%s handled=%s duration_ms=%s",
                event_name, event_id, chat_id, event_assistant_id, response.status_code, handled, duration_ms,
            )

        except asyncio.CancelledError:
            logger.info("[SSE] forward_cancelled event=%s event_id=%s", event_name, event_id)
            raise

        except httpx.HTTPError as e:
            logger.error("[SSE] forward_http_error event=%s event_id=%s error=%s: %s", event_name, event_id, type(e).__name__, e)

        except Exception:
            logger.exception("[SSE] forward_failed_unexpectedly event=%s event_id=%s", event_name, event_id)


async def start_sse_listener(
    webhook_url: str,
    max_concurrent_tasks: int = 10,
) -> None:
    """Listen to SSE events and forward valid events to the webhook."""

    logger.info(
        "[SSE] listener_started subscriptions=%s webhook_url=%s max_concurrent=%s assistant_filter=%s",
        settings.SSE_SUBSCRIPTIONS, webhook_url, max_concurrent_tasks, settings.SSE_ASSISTANT_ID or "-",
    )

    background_tasks: set[asyncio.Task] = set()
    semaphore = asyncio.Semaphore(max_concurrent_tasks)

    # Prevent unlimited creation of pending event-processing tasks.
    max_pending_tasks = max_concurrent_tasks * 5

    # Use one shared client for all webhook requests.
    async with httpx.AsyncClient(
        timeout=300.0,
        trust_env=False,
    ) as webhook_client:
        try:
            async for event_data in get_sse_stream():
                if len(background_tasks) >= max_pending_tasks:
                    logger.error(
                        "[SSE] event_dropped reason=backlog_full pending=%s limit=%s event=%s event_id=%s",
                        len(background_tasks), max_pending_tasks, event_data.get("event", "-"), event_data.get("id", "-"),
                    )
                    continue

                task = asyncio.create_task(
                    process_event(
                        event_data=event_data,
                        webhook_url=webhook_url,
                        client=webhook_client,
                        semaphore=semaphore,
                    )
                )

                background_tasks.add(task)
                task.add_done_callback(
                    background_tasks.discard
                )

        except asyncio.CancelledError:
            logger.info("[SSE] listener_stopping pending_tasks=%s", len(background_tasks))

            for task in background_tasks:
                task.cancel()

            if background_tasks:
                await asyncio.gather(
                    *background_tasks,
                    return_exceptions=True,
                )

            raise

        finally:
            # Wait for any remaining tasks during normal listener exit.
            if background_tasks:
                await asyncio.gather(
                    *background_tasks,
                    return_exceptions=True,
                )