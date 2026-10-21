"""Orchestrator phases: which phase the graph is in, and what each phase requires of the decision."""

from __future__ import annotations

from app.constants import (
    FALLBACK_CLARIFICATION_QUESTION,
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
from app.schemas.orchestrator import (
    ExecutionPlan,
    ExecutionStage,
    ExtractedEntities,
    OrchestratorDecision,
    PlanStep,
)


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


def fallback_decision(state: GraphState, phase: str) -> OrchestratorDecision:
    """Safe default when the model never produced a valid decision: never dispatch on a guess.

    replan: re-run exactly what the sufficiency check asked for. human_in_the_loop: ask its question.
    Otherwise (or if no valid re-fetch plan can be built): ask the RM to rephrase.
    """
    entities = state.get("entities") or ExtractedEntities()
    query = state.get("resolved_query") or state.get("user_message", "")
    verdict = state.get("sufficiency")

    if phase == PHASE_REPLAN and verdict is not None and verdict.refetch_instructions:
        steps = [
            PlanStep(
                agent=item.agent,
                objective=f"{item.missing_information} (for the request: {query})",
                extra_instruction=item.instruction,
            )
            for item in verdict.refetch_instructions
        ]
        try:
            return OrchestratorDecision(
                reasoning="Fallback: re-fetch plan built directly from the sufficiency instructions.",
                intent=INTENT_DATA_REQUEST,
                resolved_query=query,
                entities=entities,
                plan=ExecutionPlan(stages=[ExecutionStage(steps=steps)]),
            )
        except ValueError:  # for example a portfolio re-fetch without IDs: ask the RM instead
            pass

    question = FALLBACK_CLARIFICATION_QUESTION
    if phase == PHASE_HUMAN_IN_THE_LOOP and verdict is not None and verdict.human_question is not None:
        question = verdict.human_question.question
    return OrchestratorDecision(
        reasoning="Fallback: no valid decision from the model; asking the RM instead of guessing.",
        intent=INTENT_NEEDS_CLARIFICATION,
        resolved_query=query,
        entities=entities,
        clarification_question=question,
    )
