import asyncio
import json
import time
from typing import AsyncIterator

import httpx

from app.config import settings
from app.utils.logger import logger


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
        logger.error(f"JSON parsing error: {e}")
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
                        "SSE connection established successfully. "
                        f"Status={response.status_code}"
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
                            logger.info(
                                "Reconnecting SSE stream before timeout"
                            )
                            break

            # Avoid a tight reconnect loop if the server closes immediately.
            await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("SSE listener was cancelled")
            raise

        except httpx.HTTPStatusError as e:
            logger.warning(
                "SSE server returned an unsuccessful response. "
                f"Status={e.response.status_code}. "
                "Retrying in 5 seconds..."
            )
            await asyncio.sleep(5)

        except Exception as e:
            logger.warning(
                f"SSE connection error: {e}. "
                "Retrying in 5 seconds..."
            )
            await asyncio.sleep(5)


async def process_event(
    event_data: dict,
    webhook_url: str,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> None:
    """Validate and forward an event to the webhook."""

    async with semaphore:
        try:
            logger.debug("New SSE event received")

            payload = event_data.get("payload")

            if not isinstance(payload, dict):
                logger.warning(
                    "Ignoring SSE event because payload is missing "
                    "or invalid"
                )
                return

            event_assistant_id = payload.get("assistantId")

            # Filter by assistant only when SSE_ASSISTANT_ID is configured.
            if (
                settings.SSE_ASSISTANT_ID
                and event_assistant_id != settings.SSE_ASSISTANT_ID
            ):
                logger.info(
                    "Ignoring SSE event for a different assistant. "
                    f"Received assistantId={event_assistant_id}"
                )
                return

            logger.info(
                "Accepted SSE event. "
                f"assistantId={event_assistant_id}"
            )

            response = await client.post(
                webhook_url,
                json=event_data,
                headers={
                    "Content-Type": "application/json",
                },
                timeout=300.0,
            )

            if not response.is_success:
                logger.error(
                    f"Webhook error: {response.status_code} - "
                    f"{response.text}"
                )
                return

            try:
                response_content = response.json()
            except json.JSONDecodeError:
                response_content = response.text

            logger.info(
                f"Webhook response: {response_content}"
            )

        except asyncio.CancelledError:
            logger.info("Event processing task was cancelled")
            raise

        except httpx.HTTPError as e:
            logger.error(
                f"HTTP error while forwarding SSE event: {e}"
            )

        except Exception as e:
            logger.error(
                f"Error processing SSE event: {e}"
            )


async def start_sse_listener(
    webhook_url: str,
    max_concurrent_tasks: int = 10,
) -> None:
    """Listen to SSE events and forward valid events to the webhook."""

    logger.info(
        f"Starting SSE listener for: {settings.SSE_SUBSCRIPTIONS}"
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
                    logger.warning(
                        "Maximum pending task limit reached. "
                        "Skipping the SSE event."
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
            logger.info("Stopping SSE listener")

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