import asyncio
import json
import time
from typing import AsyncIterator
from urllib.parse import quote

import httpx

from app.config import settings
from app.utils.logger import logger


def _resolve_ssl_verify() -> bool | str:
    cert_path = (settings.SSL_CA_CERT_PATH or "").strip()
    if cert_path:
        return cert_path
    return settings.SSL_VERIFY


def _parse_sse_event(event_data: str) -> dict | None:
    """Parse a single SSE event string into a dict."""
    event = {}

    for line in event_data.split("\n"):
        if ":" in line:
            key, value = line.split(": ", 1)
            event[key] = value

    if "data" not in event:
        return None

    try:
        return json.loads(event["data"])
    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing error: {e}")
        return None


def _process_buffer(buffer: str) -> tuple[list[dict], str]:
    """Extract complete SSE events from buffer. Return parsed events and remaining buffer."""
    events = []

    while "\n\n" in buffer:
        event_data, buffer = buffer.split("\n\n", 1)

        if event_data.strip():
            parsed = _parse_sse_event(event_data)

            if parsed is not None:
                events.append(parsed)

    return events, buffer


async def get_sse_stream() -> AsyncIterator[dict]:
    """Generates an SSE stream with automatic reconnection."""

    subscriptions = ",".join(
        quote(item.strip())
        for item in (settings.SUBSCRIPTIONS or [])
        if item.strip()
    )

    if not subscriptions:
        raise RuntimeError("SUBSCRIPTIONS is empty")

    api_base = settings.UNIQUE_API_BASE_URL.rstrip("/")
    if not api_base:
        raise RuntimeError("UNIQUE_API_BASE_URL is empty")

    api_key = (settings.UNIQUE_APP_KEY or "").strip()
    app_id = (settings.UNIQUE_APP_ID or "").strip()
    company_id = (settings.UNIQUE_COMPANY_ID or "").strip()

    missing = []
    if not api_key:
        missing.append("UNIQUE_APP_KEY")
    if not app_id:
        missing.append("UNIQUE_APP_ID")
    if not company_id:
        missing.append("UNIQUE_COMPANY_ID")
    if missing:
        raise RuntimeError(f"Missing SSE settings: {', '.join(missing)}")

    url = (
        f"{api_base}"
        f"/public/event-socket/events/stream?subscriptions={subscriptions}"
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "x-app-id": app_id,
        "x-company-id": company_id,
        "Connection": "keep-alive",
        "Accept": "text/event-stream",
    }

    while True:  # Infinite loop for automatic reconnection
        try:
            async with httpx.AsyncClient(
                timeout=300.0,
                trust_env=False,
                verify=_resolve_ssl_verify(),
            ) as client:  # nosec B501

                logger.info("Connecting to upstream SSE stream", extra={"url": url})

                async with client.stream("GET", url, headers=headers) as response:
                    response.raise_for_status()
                    buffer = ""
                    last_activity = time.time()

                    async for chunk in response.aiter_bytes():
                        # Reset timer on each received chunk
                        last_activity = time.time()

                        buffer += chunk.decode("utf-8")

                        events, buffer = _process_buffer(buffer)

                        for event in events:
                            logger.info(
                                "Received SSE event",
                                extra={
                                    "event": event.get("event") or event.get("type") or "unknown",
                                    "event_id": event.get("id") or event.get("eventId") or "",
                                },
                            )
                            yield event

                        # Reconnect after prolonged inactivity to avoid upstream idle timeouts.
                        if time.time() - last_activity > 50:
                            logger.info("Proactive reconnection to avoid timeout")
                            break

        except Exception as e:
            logger.warning(
                f"Connection error: {e}. Retrying in 5 seconds..."
            )
            await asyncio.sleep(5)


async def process_event(
    event_data: dict,
    client: httpx.AsyncClient,
    webhook_url: str,
    semaphore: asyncio.Semaphore,
) -> None:
    """Process an event and send it to the webhook."""

    async with semaphore:
        try:
            logger.info(
                "Forwarding SSE event to webhook",
                extra={
                    "webhook_url": webhook_url,
                    "event": event_data.get("event") or event_data.get("type") or "unknown",
                    "event_id": event_data.get("id") or event_data.get("eventId") or "",
                },
            )

            response = await client.post(
                webhook_url,
                json=event_data,
                headers={"Content-Type": "application/json"},
                timeout=300,
            )

            logger.info(
                "Webhook response received",
                extra={
                    "status_code": response.status_code,
                    "body": response.text,
                },
            )

            if response.status_code != 200:
                logger.error(
                    f"Webhook error: {response.status_code} - {response.text}"
                )

        except Exception as e:
            logger.error(f"Error sending to webhook: {e}")


async def start_sse_listener(
    webhook_url: str,
    max_concurrent_tasks: int = 10,
) -> None:
    """Listens to SSE events and forwards them to the webhook."""

    logger.info(
        "Starting SSE listener",
        extra={
            "subscriptions": settings.SUBSCRIPTIONS,
            "webhook_url": webhook_url,
            "max_concurrent_tasks": max_concurrent_tasks,
        },
    )

    background_tasks = set()
    semaphore = asyncio.Semaphore(max_concurrent_tasks)

    async with httpx.AsyncClient(
        timeout=30.0,
        trust_env=False,
        verify=_resolve_ssl_verify(),
    ) as webhook_client:
        async for event_data in get_sse_stream():
            task = asyncio.create_task(
                process_event(
                    event_data,
                    webhook_client,
                    webhook_url,
                    semaphore,
                )
            )

            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)