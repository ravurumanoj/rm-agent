"""Shared node plumbing: tracing and logging."""

from __future__ import annotations

import functools
import time
from contextlib import nullcontext
from typing import Any, Awaitable, Callable

from app.components.llm_call_context import llm_call
from app.constants import STEP_LABELS
from app.observability import Attr, SpanKind, SpanName, record_failure, record_output, start_span
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


def _node_input(state: Any) -> str | None:
    """What the node works on: the RM request, in its resolved form once the orchestrator has set it."""
    if not isinstance(state, dict):
        return None
    return state.get("resolved_query") or state.get("user_message") or None


def _node_span(name: str, state: Any):
    return start_span(
        SpanName.node(name),
        kind=SpanKind.CHAIN,
        attributes={Attr.GRAPH_NODE: name},
        input_value=_node_input(state),
        graph_node=name,
    )


def traced_node(name: str, describe: StepDescriber | None = None, *, span: bool = True) -> Callable[[NodeFn], NodeFn]:
    """Wrap a node in a tracing span, a start/failure log line and, when a chat is attached, a progress step.

    describe(update) gives the text the step shows once the node has finished, e.g. where the request was routed.
    The span is a node of the agent graph, with the request as input and the node's update as output. span=False
    leaves it out for nodes with no work of their own or whose agent opens its own span (LLM calls are still
    named after the node).
    """

    def decorator(fn: NodeFn) -> NodeFn:
        @functools.wraps(fn)
        async def wrapper(state: Any) -> dict[str, Any]:
            started = time.perf_counter()
            steps = current_step_logger.get() if name in STEP_LABELS else None
            # The progress step is opened inside the span, so the node's latency includes it and nodes add up.
            with _node_span(name, state) if span else nullcontext() as node_span:
                if steps:
                    await steps.start(name, STEP_LABELS[name])
                try:
                    with llm_call(name):  # LLM spans inside this node are named after it, e.g. llm.final_agent
                        update = await fn(state)
                    logger.debug(
                        "[GRAPH] node_completed node=%s duration_ms=%s update_keys=%s",
                        name, int((time.perf_counter() - started) * 1000), sorted(update),
                    )
                    if steps:
                        await steps.finish(name, text=_step_text(describe, update))
                    if node_span is not None:
                        record_output(node_span, {key: value for key, value in update.items() if key != "node_trace"})
                    return update
                except Exception as exc:
                    logger.error(
                        "[GRAPH] node_failed node=%s duration_ms=%s error=%s",
                        name, int((time.perf_counter() - started) * 1000), exc, exc_info=True,
                    )
                    if node_span is not None:
                        record_failure(node_span, exc, where=f"graph node {name} failed")
                    if steps:
                        await steps.finish(name, failed=True)
                    raise

        return wrapper

    return decorator
