"""Entry point used by routes, streaming and the webhook: runs one turn through the workflow graph."""

from __future__ import annotations

import time
from typing import Any

from app.config import settings
from app.constants import GRAPH_RECURSION_LIMIT
from app.graph.builder import get_compiled_graph
from app.graph.state import GraphState
from app.schemas.evaluation import EvaluationContext
from app.schemas.internal import TurnResult
from app.schemas.orchestrator import PendingClarification
from app.services.checkpointer import in_memory_checkpointer
from app.services.evaluation import create_default_evaluation_manager
from app.observability import query_context, set_current_attributes
from app.services.observability import audit_query, record_query_outcome
from app.utils.logger import logger, preview


def _load_session(session_id: str, history_block: str) -> tuple[str, PendingClarification | None]:
    """Restore short-term memory: conversation history and any clarification awaiting an answer."""
    if not settings.GRAPH_CHECKPOINTER_ENABLED:
        return history_block, None

    snapshot = in_memory_checkpointer.load_snapshot(session_id)
    pending_raw = snapshot.get("pending_clarification")
    pending = PendingClarification.model_validate(pending_raw) if pending_raw else None
    history = history_block if history_block.strip() else in_memory_checkpointer.build_history_block(session_id)
    return history, pending


def _persist_session(session_id: str, message: str, final_state: GraphState) -> None:
    """Save the turn, retrying once; if it still fails, the answer is delivered anyway and only resume is lost."""
    if not settings.GRAPH_CHECKPOINTER_ENABLED:
        return
    for attempt in (1, 2):
        try:
            _write_session(session_id, message, final_state)
            return
        except Exception:
            if attempt == 2:
                logger.exception(
                    "[GRAPH] checkpoint_write_failed session_id=%s; answering anyway, resume unavailable", session_id
                )


def _write_session(session_id: str, message: str, final_state: GraphState) -> None:
    new_pending = final_state.get("new_pending_clarification")
    plan = final_state.get("plan")
    in_memory_checkpointer.save_snapshot(
        session_id,
        {
            "outcome": final_state.get("outcome", ""),
            "execution_mode": plan.mode if plan else "",
            "replan_count": final_state.get("replan_count", 0),
            "last_reply": final_state.get("final_output", ""),
            "pending_clarification": new_pending.model_dump() if new_pending else None,
        },
    )
    in_memory_checkpointer.append_turn(
        session_id, user_message=message, assistant_message=final_state.get("final_output", "")
    )


async def _evaluate(message: str, final_state: GraphState, session_id: str) -> list[dict[str, Any]]:
    context = EvaluationContext(
        question=message,
        answer=final_state.get("final_output", ""),
        citations=final_state.get("citations", []),
        metadata={"session_id": session_id, "outcome": final_state.get("outcome", "")},
    )
    results = await create_default_evaluation_manager(fail_open=True).run(context)
    return [
        {
            "name": result.name,
            "passed": result.passed,
            "score": result.score,
            "severity": result.severity.value,
            "summary": result.summary,
            "details": result.details,
        }
        for result in results
    ]


async def run_turn(
    session_id: str,
    message: str,
    *,
    tool_outputs: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    history_block: str = "",
) -> TurnResult:
    """Run one RM message through the orchestrator graph and return reply, citations and evaluations.

    One query = one trace: the caller (webhook, /agent/chat, /agent/stream) opens the root span with start_trace,
    so it also covers the work around the turn (history load, reply delivery). The outcome and the reply are
    recorded on that span; the correlation id bound here is on every span and log line.
    """
    user_id = str((metadata or {}).get("user_id") or "")
    with query_context(session_id=session_id, user_id=user_id):
        set_current_attributes({"app.query.history_present": bool(history_block.strip())})
        started = time.perf_counter()
        logger.debug(
            "[GRAPH] turn_input session_id=%s message=%s metadata=%s history=%s",
            session_id, preview(message), preview(metadata or {}), preview(history_block),
        )
        if tool_outputs:
            logger.warning("[GRAPH] tool_outputs_ignored count=%s; agents fetch their own data", len(tool_outputs))

        try:
            history, pending = _load_session(session_id, history_block)
            initial_state: GraphState = {
                "session_id": session_id,
                "user_message": message,
                "history_block": history,
                "metadata": metadata or {},
                "pending_clarification": pending,
                "replan_count": 0,
                "current_stage": 0,
                "agent_results": {},
            }
            final_state: GraphState = await get_compiled_graph().ainvoke(
                initial_state, config={"recursion_limit": GRAPH_RECURSION_LIMIT}
            )

            evaluations = await _evaluate(message, final_state, session_id)
            _persist_session(session_id, message, final_state)
            result = TurnResult(
                reply=final_state.get("final_output", ""),
                citations=final_state.get("citations", []),
                evaluations=evaluations,
            )
            logger.info(
                "[GRAPH] turn_completed session_id=%s duration_ms=%s outcome=%s replans=%s citations=%s "
                "evaluations_failed=%s trace=%s reply=%s",
                session_id, int((time.perf_counter() - started) * 1000), final_state.get("outcome", ""),
                final_state.get("replan_count", 0), len(result.citations),
                [item["name"] for item in evaluations if not item.get("passed", True)],
                final_state.get("node_trace", []), preview(result.reply),
            )
            signals = record_query_outcome(final_state)
            audit_query(question=message, final_state=final_state, signals=signals)
            return result
        except Exception:
            # The caller's root span records the failure (with where it happened) when the exception reaches it.
            logger.exception(
                "[GRAPH] turn_failed session_id=%s duration_ms=%s message=%s",
                session_id, int((time.perf_counter() - started) * 1000), preview(message),
            )
            raise
