"""Builds the variables each orchestrator prompt template needs."""

from __future__ import annotations

import re

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
from app.prompts.agent_catalog import format_agent_catalog
from app.schemas.orchestrator import ExtractedEntities, OrchestratorDecision

# Delimiter tags the orchestrator templates wrap untrusted text in; stripped from that text so it cannot close
# its own block and pose as instructions.
_DELIMITER_TAGS = re.compile(
    r"</?\s*(rm_message|rm_answer|original_request|your_question|known_entities|conversation_history|"
    r"request_context|standalone_request|previous_plan|sufficiency_report|agent_results)\b[^>]*>",
    re.IGNORECASE,
)


def neutralize_delimiters(text: str) -> str:
    return _DELIMITER_TAGS.sub("", text)


def build_orchestrator_variables(state: GraphState) -> dict[str, str]:
    """Superset of variables for all phases; each template uses the ones it needs."""
    pending = state.get("pending_clarification")
    entities = state.get("entities") or (pending.entities if pending else ExtractedEntities())
    history = neutralize_delimiters(state.get("history_block") or "").strip()
    user_message = neutralize_delimiters(state.get("user_message", ""))

    return {
        "agent_catalog": format_agent_catalog(),
        "output_schema": schema_instructions(OrchestratorDecision),
        "history": history or "(no earlier conversation)",
        "metadata_block": format_metadata_block(state.get("metadata") or {}),
        "user_message": user_message,
        "original_query": neutralize_delimiters(pending.original_query) if pending else "",
        "pending_question": pending.question if pending else "",
        "known_entities": format_entities(entities),
        "resolved_query": neutralize_delimiters(state.get("resolved_query") or user_message),
        "previous_plan": format_plan(state.get("plan")),
        "sufficiency_report": neutralize_delimiters(format_sufficiency_report(state.get("sufficiency"))),
        "results_overview": neutralize_delimiters(format_results_overview(state.get("agent_results") or {})),
        "replan_count": str(state.get("replan_count", 0)),
        "replan_attempt": str(state.get("replan_count", 0) + 1),
        "max_replans": str(settings.AGENT_MAX_REPLAN_LOOPS),
    }
