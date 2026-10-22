"""Shared node plumbing: tracing and logging."""

from __future__ import annotations

import functools
import time
from typing import Any, Awaitable, Callable

from app.components.llm_call_context import llm_call
from app.constants import STEP_LABELS
from app.observability import Attr, SpanKind, SpanName, start_span
from app.services.unique_chat import current_step_logger
from app.utils.logger import logger

NodeFn = Callable[[Any], Awaitable[dict[str, Any]]]
StepDescriber = Callable[[dict[str, Any]], str | None]


def _step_text(describe: StepDescriber | None, update: dict[str, Any]) -> str | None:
    """Completed-step text from the node's update; a describer bug must never fail the node."""
    if describe is None:
        return None
    try:
        return describe(update)
    except Exception:
        logger.warning("[GRAPH] step_describe_failed", exc_info=True)
        return None


def traced_node(name: str, describe: StepDescriber | None = None) -> Callable[[NodeFn], NodeFn]:
    """Wrap a node in a tracing span, a start/failure log line and, when a chat is attached, a progress step.

    describe(update) gives the text the step shows once the node has finished, e.g. where the request was routed.
    """

    def decorator(fn: NodeFn) -> NodeFn:
        @functools.wraps(fn)
        async def wrapper(state: Any) -> dict[str, Any]:
            started = time.perf_counter()
            steps = current_step_logger.get() if name in STEP_LABELS else None
            if steps:
                await steps.start(name, STEP_LABELS[name])
            with start_span(SpanName.node(name), kind=SpanKind.CHAIN, attributes={Attr.GRAPH_NODE: name}):
                try:
                    with llm_call(name):  # LLM spans inside this node are named after it, e.g. llm.final_agent
                        update = await fn(state)
                    logger.debug(
                        "[GRAPH] node_completed node=%s duration_ms=%s update_keys=%s",
                        name, int((time.perf_counter() - started) * 1000), sorted(update),
                    )
                    if steps:
                        await steps.finish(name, text=_step_text(describe, update))
                    return update
                except Exception as exc:
                    logger.error(
                        "[GRAPH] node_failed node=%s duration_ms=%s error=%s",
                        name, int((time.perf_counter() - started) * 1000), exc, exc_info=True,
                    )
                    if steps:
                        await steps.finish(name, failed=True)
                    raise

        return wrapper

    return decorator
