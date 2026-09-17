import asyncio
import json
import time
from typing import AsyncIterator

import httpx

from app.config import settings
from app.utils.logger import logger


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

    subscriptions = ",".join(settings.genix.subscriptions)

    url = (
        f"{settings.genix.api_base}"
        f"/public/event-socket/events/stream?subscriptions={subscriptions}"
    )

    headers = {
        "Authorization": f"Bearer {settings.genix.api_key.get_secret_value()}",
        "x-app-id": settings.genix.app_id,
        "x-company-id": settings.genix.company_id,
        "Connection": "keep-alive",
        "Accept": "text/event-stream",
    }

    while True:  # Infinite loop for automatic reconnection
        try:
            async with httpx.AsyncClient(
                timeout=300.0,
                trust_env=False,
                verify=settings.requests_ca_bundle,
            ) as client:  # nosec B501

                last_activity = time.time()

                async with client.stream("GET", url, headers=headers) as response:
                    buffer = ""

                    async for chunk in response.aiter_bytes():
                        # Reset timer on each received chunk
                        last_activity = time.time()

                        buffer += chunk.decode("utf-8")

                        events, buffer = _process_buffer(buffer)

                        for event in events:
                            yield event

                        # Reconnect after 50s of activity to prevent 60s timeout
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
            logger.debug("New event received")
            logger.debug(f"Event details: {event_data}")

            response = await client.post(
                webhook_url,
                json=event_data,
                headers={"Content-Type": "application/json"},
                timeout=300,
            )

            logger.info(f"Webhook response: {response.json()}")

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
        f"Starting SSE listener for: {settings.genix.subscriptions}"
    )

    background_tasks = set()
    semaphore = asyncio.Semaphore(max_concurrent_tasks)

    async for event_data in get_sse_stream():
        async with httpx.AsyncClient(
            timeout=30.0,
            trust_env=False,
        ) as webhook_client:

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