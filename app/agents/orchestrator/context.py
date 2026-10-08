"""Builds the variables each orchestrator prompt template needs."""

from __future__ import annotations

from app.agents.formatting import (
    format_entities,
    format_metadata_block,
    format_plan,
    format_results_overview,
    format_sufficiency_report,
)
from app.components.structured_output import schema_instructions
from app.config import settings
from app.graph.state import GraphState
from app.prompts.common import format_agent_catalog
from app.schemas.orchestrator import ExtractedEntities, OrchestratorDecision


def build_orchestrator_variables(state: GraphState) -> dict[str, str]:
    """Superset of variables for all phases; each template uses the ones it needs."""
    pending = state.get("pending_clarification")
    entities = state.get("entities") or (pending.entities if pending else ExtractedEntities())
    history = (state.get("history_block") or "").strip()
    user_message = state.get("user_message", "")

    return {
        "agent_catalog": format_agent_catalog(),
        "output_schema": schema_instructions(OrchestratorDecision),
        "history_block": f"Conversation so far:\n{history}\n\n" if history else "",
        "metadata_block": format_metadata_block(state.get("metadata") or {}),
        "user_message": user_message,
        "original_query": pending.original_query if pending else "",
        "pending_question": pending.question if pending else "",
        "known_entities": format_entities(entities),
        "resolved_query": state.get("resolved_query") or user_message,
        "previous_plan": format_plan(state.get("plan")),
        "sufficiency_report": format_sufficiency_report(state.get("sufficiency")),
        "results_overview": format_results_overview(state.get("agent_results") or {}),
        "replan_count": str(state.get("replan_count", 0)),
        "replan_attempt": str(state.get("replan_count", 0) + 1),
        "max_replans": str(settings.AGENT_MAX_REPLAN_LOOPS),
    }
