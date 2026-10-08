"""Orchestrator phases: which phase the graph is in, and what each phase requires of the decision."""

from __future__ import annotations

from app.constants import (
    INTENT_DATA_REQUEST,
    INTENT_NEEDS_CLARIFICATION,
    PHASE_CLARIFICATION_FOLLOWUP,
    PHASE_HUMAN_IN_THE_LOOP,
    PHASE_REPLAN,
    PHASE_ROUTING,
    VERDICT_ASK_HUMAN,
    VERDICT_REPLAN,
)
from app.graph.state import GraphState
from app.schemas.orchestrator import OrchestratorDecision


def resolve_phase(state: GraphState) -> str:
    """Sufficiency feedback wins over everything; otherwise a pending question means a follow-up."""
    verdict = state.get("sufficiency")
    if verdict is not None and verdict.verdict == VERDICT_REPLAN:
        return PHASE_REPLAN
    if verdict is not None and verdict.verdict == VERDICT_ASK_HUMAN:
        return PHASE_HUMAN_IN_THE_LOOP
    if state.get("pending_clarification") is not None:
        return PHASE_CLARIFICATION_FOLLOWUP
    return PHASE_ROUTING


def validate_for_phase(decision: OrchestratorDecision, state: GraphState, phase: str) -> OrchestratorDecision:
    """Reject decisions that break the phase contract; the message is sent back to the model to fix."""
    if phase == PHASE_REPLAN:
        if decision.intent != INTENT_DATA_REQUEST:
            raise ValueError("In the replan phase intent must be data_request.")
        allowed = {item.agent for item in state["sufficiency"].refetch_instructions}
        extra = sorted(set(decision.plan.agents) - allowed)
        if extra:
            raise ValueError(f"The plan may only include agents named in the re-fetch instructions; remove {extra}.")
    if phase == PHASE_HUMAN_IN_THE_LOOP and decision.intent != INTENT_NEEDS_CLARIFICATION:
        raise ValueError("In the human-in-the-loop phase intent must be needs_clarification.")
    return decision
