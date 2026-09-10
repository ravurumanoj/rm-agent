from __future__ import annotations

import json
from typing import Any, AsyncGenerator

from app.agents.orchestrator import run_turn
from app.utils.logger import logger


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
        logger.error("stream_chat_events failed: %s", exc, exc_info=True)
        yield sse_data({"type": "error", "message": str(exc)})
        return

    yield sse_data({"type": "step", "node": "execute_agents", "state": "done"})
    yield sse_data({"type": "step", "node": "synthesizer", "state": "running"})

    full_response = result.reply or ""
    for token in _chunk_text(full_response):
        yield sse_data({"type": "token", "content": token})

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
