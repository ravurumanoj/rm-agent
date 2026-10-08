"""Entry point used by routes, streaming and the webhook: runs one turn through the workflow graph."""

from __future__ import annotations

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
from app.services.tracing import operation_span, record_exception
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
    if not settings.GRAPH_CHECKPOINTER_ENABLED:
        return
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
    """Run one RM message through the orchestrator graph and return reply, citations and evaluations."""
    with operation_span(
        "agent.turn", kind="CHAIN", attributes={"session.id": session_id, "app.turn.history_present": bool(history_block.strip())}
    ) as span:
        logger.info("[GRAPH] turn_started session_id=%s message=%s", session_id, preview(message))
        logger.debug("[GRAPH] turn_input metadata=%s history=%s", preview(metadata or {}), preview(history_block))
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
                "[GRAPH] turn_completed session_id=%s outcome=%s citations=%s trace=%s reply=%s",
                session_id,
                final_state.get("outcome", ""),
                len(result.citations),
                final_state.get("node_trace", []),
                preview(result.reply),
            )
            logger.debug("[GRAPH] turn_evaluations=%s", preview(evaluations))
            if span is not None:
                span.set_attribute("app.turn.outcome", final_state.get("outcome", ""))
                span.set_attribute("app.citation_count", len(result.citations))
            return result
        except Exception as exc:
            record_exception(span, exc)
            logger.exception("[GRAPH] turn_failed session_id=%s", session_id)
            raise
