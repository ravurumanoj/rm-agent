"""Entry point used by app.routes.agent — compiles/reuses the graph and runs one turn."""

from typing import Any

from app.agents.clarification import clarification_agent
from app.agents.execution import evaluate_sufficiency_for_route, execute_agents, has_effective_external_tool_outputs
from app.agents.query_details import resolve_portfolio_context
from app.agents.router import router_agent
from app.agents.synthesizer import synthesizer_agent
from app.config import settings
from app.constants import DATA_ROUTES, MAX_RETRIES, ROUTE_OUT_OF_SCOPE
from app.prompts.replan import REPLAN_INSTRUCTION_TEMPLATE
from app.schemas.evaluation import EvaluationContext
from app.schemas.internal import AgentState, TurnResult
from app.services.checkpointer import in_memory_checkpointer
from app.services.evaluation import create_default_evaluation_manager
from app.utils.logger import logger


def _persist_turn_state(session_id: str, message: str, state: AgentState) -> None:
    if not settings.GRAPH_CHECKPOINTER_ENABLED:
        return

    in_memory_checkpointer.save_snapshot(
        session_id,
        {
            "route": state.get("route", ""),
            "execution_mode": state.get("execution_mode", ""),
            "producer": state.get("producer", ""),
            "retry_count": state.get("retry_count", 0),
            "last_reply": state.get("final_output", ""),
        },
    )
    in_memory_checkpointer.append_turn(
        session_id,
        user_message=message,
        assistant_message=state.get("final_output", ""),
    )
    logger.debug("[CHKPT] state_persisted session_id=%s", session_id)


async def run_turn(
    session_id: str,
    message: str,
    *,
    tool_outputs: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    history_block: str = "",
) -> TurnResult:
    """Run one orchestration turn with routing, optional clarification, and re-fetch loop."""
    logger.info("[ORCH] turn_started session_id=%s", session_id)

    effective_history_block = history_block
    if settings.GRAPH_CHECKPOINTER_ENABLED and not effective_history_block.strip():
        restored = in_memory_checkpointer.load_snapshot(session_id)
        effective_history_block = in_memory_checkpointer.build_history_block(session_id)
        if restored:
            logger.info(
                "[CHKPT] state_restored session_id=%s route=%s updated_at=%s",
                session_id,
                restored.get("route", ""),
                restored.get("updated_at", ""),
            )

    normalized_metadata = metadata or {}
    state: AgentState = {
        "session_id": session_id,
        "user_message": message,
        "metadata": normalized_metadata,
        "history_block": effective_history_block,
        "requested_portfolio_ids": [],
        "known_portfolio_ids": [],
        "selected_portfolio_ids": [],
        "unmatched_portfolio_ids": [],
        "needs_portfolio_clarification": False,
        "portfolio_clarification_question": "",
        "tool_outputs": tool_outputs or [],
        "external_tool_outputs": tool_outputs or [],
        "portfolio_output": {},
        "crm_output": {},
        "citation_references": [],
        "cited_references": [],
        "needs_clarification": False,
        "clarification_question": "",
        "replan_instruction": "",
        "retrieval_missing_sections": [],
        "retry_count": 0,
        "final_output": "",
    }

    state.update(await router_agent(state))
    state.update(
        resolve_portfolio_context(
            user_message=message,
            metadata=normalized_metadata,
            route=state.get("route", ""),
        )
    )
    logger.info(
        "[ORCH] route_selected route=%s mode=%s producer=%s portfolio_ids=%s",
        state.get("route", ""),
        state.get("execution_mode", ""),
        state.get("producer", ""),
        state.get("selected_portfolio_ids", []),
    )

    # Deterministic tool_outputs supplied by the caller mean data is already
    # fetched; skip clarification gates entirely since there is nothing to ask.
    has_preloaded_data = has_effective_external_tool_outputs(state.get("external_tool_outputs") or [])

    if state.get("needs_portfolio_clarification") and not has_preloaded_data:
        question = (
            state.get("portfolio_clarification_question")
            or "Please specify the portfolio ID you want me to use for this request."
        )
        state["needs_clarification"] = True
        state["clarification_question"] = question
        state["final_output"] = question
        state["cited_references"] = []
        state["evaluation_results"] = []
        logger.info("[ORCH] portfolio_clarification_required session_id=%s", session_id)
        _persist_turn_state(session_id, message, state)
        return TurnResult(reply=question, citations=[], evaluations=[])

    if state.get("route") in DATA_ROUTES and not has_preloaded_data:
        state.update(await clarification_agent(state))
        if state.get("needs_clarification"):
            question = (state.get("clarification_question") or "").strip()
            fallback = "Please clarify which specific client, portfolio, or date range you want."
            final_question = question or fallback
            state["final_output"] = final_question
            state["cited_references"] = []
            state["evaluation_results"] = []
            logger.info("[ORCH] clarification_required session_id=%s", session_id)
            _persist_turn_state(session_id, message, state)
            return TurnResult(reply=final_question, citations=[], evaluations=[])

    route = state.get("route", "")
    if route == ROUTE_OUT_OF_SCOPE:
        state.update(await synthesizer_agent(state))
    else:
        run_targets = None
        replan_instruction = ""
        missing_desc = ""

        for attempt in range(MAX_RETRIES + 1):
            state["retry_count"] = attempt
            logger.info("[ORCH] execute_attempt=%s route=%s", attempt + 1, route)
            state.update(
                await execute_agents(
                    state,
                    route=route,
                    execution_mode=state.get("execution_mode", ""),
                    producer=state.get("producer", ""),
                    targets=run_targets,
                    replan_instruction=replan_instruction,
                )
            )

            is_sufficient, missing_desc, missing_targets = evaluate_sufficiency_for_route(
                route,
                state.get("portfolio_output") if isinstance(state.get("portfolio_output"), dict) else {},
                state.get("crm_output") if isinstance(state.get("crm_output"), dict) else {},
            )
            if is_sufficient or attempt >= MAX_RETRIES:
                logger.info(
                    "[ORCH] sufficiency_result sufficient=%s missing=%s",
                    is_sufficient,
                    missing_desc,
                )
                if not is_sufficient and missing_desc:
                    state["retrieval_missing_sections"] = [
                        section.strip() for section in missing_desc.split(" and ") if section.strip()
                    ]
                break

            run_targets = missing_targets
            client_id = str((state.get("metadata") or {}).get("client_id") or "unknown")
            replan_instruction = REPLAN_INSTRUCTION_TEMPLATE.format(
                missing=missing_desc,
                client_id=client_id,
            )
            state["replan_instruction"] = replan_instruction
            logger.info("[ORCH] replanning_required missing=%s targets=%s", missing_desc, sorted(missing_targets))

        state.update(await synthesizer_agent(state))

    eval_manager = create_default_evaluation_manager(fail_open=True)
    eval_context = EvaluationContext(
        question=message,
        answer=state.get("final_output", ""),
        citations=state.get("cited_references", []),
        metadata={
            "session_id": session_id,
            "route": state.get("route", ""),
            "execution_mode": state.get("execution_mode", ""),
            "producer": state.get("producer", ""),
        },
    )
    eval_results = await eval_manager.run(eval_context)

    serialized_evaluations = [
        {
            "name": result.name,
            "passed": result.passed,
            "score": result.score,
            "severity": result.severity.value,
            "summary": result.summary,
            "details": result.details,
        }
        for result in eval_results
    ]

    final_result = TurnResult(
        reply=state.get("final_output", ""),
        citations=state.get("cited_references", []),
        evaluations=serialized_evaluations,
    )
    _persist_turn_state(session_id, message, state)
    logger.info(
        "[ORCH] turn_completed session_id=%s reply_length=%s citations=%s evaluations=%s",
        session_id,
        len(final_result.reply),
        len(final_result.citations),
        len(final_result.evaluations),
    )
    return final_result
