"""Builds the AgentTask work orders for the current stage, including handoff from earlier results."""

from __future__ import annotations

from app.constants import AGENT_DISPLAY_NAMES, AGENT_STATUS_ERROR, AGENT_STATUS_NO_DATA, HANDOFF_MAX_CHARS
from app.graph.state import GraphState
from app.schemas.agent_io import AgentResult, AgentTask
from app.schemas.orchestrator import ExtractedEntities


def build_handoff_context(results: dict[str, AgentResult], *, max_chars: int = HANDOFF_MAX_CHARS) -> str:
    """Findings of agents that are not running in this stage, trimmed to a shared budget."""
    usable = [
        (agent, result)
        for agent, result in results.items()
        if result.summary.strip() and result.status not in (AGENT_STATUS_ERROR, AGENT_STATUS_NO_DATA)
    ]
    if not usable:
        return ""
    budget = max(200, max_chars // len(usable))
    lines = []
    for agent, result in usable:
        summary = " ".join(result.summary.split())
        if len(summary) > budget:
            summary = summary[: budget - 3] + "..."
        lines.append(f"- {AGENT_DISPLAY_NAMES.get(agent, agent)}: {summary}")
    return "\n".join(lines)


def build_stage_tasks(state: GraphState) -> list[AgentTask]:
    plan = state.get("plan")
    stage_index = state.get("current_stage", 0)
    if plan is None or stage_index >= len(plan.stages):
        return []

    stage = plan.stages[stage_index]
    running = {step.agent for step in stage.steps}
    handoff = build_handoff_context(
        {agent: result for agent, result in (state.get("agent_results") or {}).items() if agent not in running}
    )
    attempt = state.get("replan_count", 0) + 1
    entities = state.get("entities") or ExtractedEntities()
    query = state.get("resolved_query") or state.get("user_message", "")

    return [
        AgentTask(
            agent=step.agent,
            objective=step.objective,
            user_query=query,
            entities=entities,
            handoff_context=handoff,
            extra_instruction=step.extra_instruction,
            attempt=attempt,
            metadata=state.get("metadata") or {},
        )
        for step in stage.steps
    ]
