"""Shared node plumbing: tracing and logging."""

from __future__ import annotations

import functools
import time
from typing import Any, Awaitable, Callable

from app.constants import STEP_LABELS
from app.services.tracing import operation_span, record_exception
from app.services.unique_chat import current_step_logger
from app.utils.logger import logger

NodeFn = Callable[[Any], Awaitable[dict[str, Any]]]


def traced_node(name: str) -> Callable[[NodeFn], NodeFn]:
    """Wrap a node in a tracing span, a start/failure log line and, when a chat is attached, a progress step."""

    def decorator(fn: NodeFn) -> NodeFn:
        @functools.wraps(fn)
        async def wrapper(state: Any) -> dict[str, Any]:
            logger.debug("[GRAPH] node_started node=%s", name)
            started = time.perf_counter()
            steps = current_step_logger.get() if name in STEP_LABELS else None
            if steps:
                await steps.start(name, STEP_LABELS[name])
            with operation_span(f"graph.node.{name}", kind="CHAIN", attributes={"graph.node": name}) as span:
                try:
                    update = await fn(state)
                    logger.debug(
                        "[GRAPH] node_completed node=%s duration_ms=%s update_keys=%s",
                        name, int((time.perf_counter() - started) * 1000), sorted(update),
                    )
                    if steps:
                        await steps.finish(name)
                    return update
                except Exception as exc:
                    logger.error(
                        "[GRAPH] node_failed node=%s duration_ms=%s error=%s",
                        name, int((time.perf_counter() - started) * 1000), exc, exc_info=True,
                    )
                    record_exception(span, exc)
                    if steps:
                        await steps.finish(name, failed=True)
                    raise

        return wrapper

    return decorator
