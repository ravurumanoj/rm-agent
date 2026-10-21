from __future__ import annotations

import json
import time
from typing import Any, AsyncGenerator

from app.graph.runner import run_turn
from app.observability import Attr, SpanKind, SpanName, record_error, set_attributes, start_span
from app.utils.logger import logger, preview


def sse_data(event: dict[str, Any]) -> str:
    """Serialize one SSE data envelope line."""
    return f"data: {json.dumps(event, ensure_ascii=True)}\n\n"


def _chunk_text(text: str, *, chunk_size: int = 48) -> list[str]:
    if not text:
        return []
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


async def stream_chat_events(
    *,
    session_id: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    tool_outputs: list[dict[str, Any]] | None = None,
) -> AsyncGenerator[str, None]:
    """Yield SSE events compatible with wealth app local UI stream contract."""
    with start_span(
        SpanName.step("stream_events"),
        kind=SpanKind.CHAIN,
        attributes={
            Attr.SESSION_ID: session_id,
            "app.tool_output_count": len(tool_outputs or []),
        },
    ) as span:
        started = time.perf_counter()
        yield sse_data({"type": "step", "node": "router", "state": "running"})
        yield sse_data({"type": "step", "node": "router", "state": "done"})
        yield sse_data({"type": "step", "node": "execute_agents", "state": "running"})

        try:
            result = await run_turn(
                session_id=session_id,
                message=message,
                metadata=metadata or {},
                tool_outputs=tool_outputs or [],
            )
        except Exception as exc:
            logger.error(
                "[STREAM] failed session_id=%s duration_ms=%s message=%s error=%s",
                session_id, int((time.perf_counter() - started) * 1000), preview(message), exc, exc_info=True,
            )
            record_error(span, exc)  # handled: reported to the client as an error event
            set_attributes(span, {"app.stream.success": False})
            yield sse_data({"type": "error", "message": str(exc)})
            return

        yield sse_data({"type": "step", "node": "execute_agents", "state": "done"})
        yield sse_data({"type": "step", "node": "synthesizer", "state": "running"})

        full_response = result.reply or ""
        token_count = 0
        for token in _chunk_text(full_response):
            token_count += 1
            yield sse_data({"type": "token", "content": token})
        logger.info(
            "[STREAM] completed session_id=%s duration_ms=%s token_events=%s citations=%s reply=%s",
            session_id, int((time.perf_counter() - started) * 1000), token_count, len(result.citations),
            preview(full_response),
        )
        yield sse_data({"type": "step", "node": "synthesizer", "state": "done"})
        yield sse_data(
            {
                "type": "done",
                "agent_used": "orchestrator",
                "full_response": full_response,
                "citations": result.citations,
                "evaluations": result.evaluations,
            }
        )
        set_attributes(
            span,
            {
                "app.stream.success": True,
                "app.stream.token_event_count": token_count,
                Attr.CITATION_COUNT: len(result.citations),
                "app.evaluation_count": len(result.evaluations),
            },
        )
