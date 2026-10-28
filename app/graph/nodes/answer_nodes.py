"""Reduce, sufficiency and final nodes."""

from __future__ import annotations

from typing import Any

from app.agents.final_agent import FinalAgent
from app.agents.sufficiency import SufficiencyAgent
from app.constants import (
    NODE_FINAL_AGENT,
    NODE_REDUCE_OUTPUTS,
    NODE_SUFFICIENCY,
    OUTCOME_ANSWER,
    OUTCOME_DEGRADED,
    STEP_FINAL_AGENT_NAME,
    STEP_SUFFICIENCY_NAME,
    VERDICT_ASK_HUMAN,
    VERDICT_PARTIAL,
    VERDICT_REPLAN,
    VERDICT_SUFFICIENT,
)
from app.agents.base import get_shared_llm
from app.agents.formatting import format_entities
from app.config import settings
from app.graph.nodes.common import NodeFn, traced_node
from app.graph.state import GraphState
from app.schemas.orchestrator import ExtractedEntities
from app.services.map_reduce import consolidate_evidence
from app.services.output_reducer import reduce_agent_results
from app.utils.logger import logger


@traced_node(NODE_REDUCE_OUTPUTS)
async def reduce_outputs_node(state: GraphState) -> dict[str, Any]:
    reduced = reduce_agent_results(state.get("agent_results") or {})
    evidence, reductions = reduced.evidence, 0
    if settings.MAP_REDUCE_ENABLED:
        # Level 2 map-reduce: consolidate the merged evidence when it would crowd the later prompts.
        evidence, reductions = await consolidate_evidence(
            reduced.evidence,
            get_llm=get_shared_llm,
            resolved_query=state.get("resolved_query") or state.get("user_message", ""),
            objectives=_objectives(state),
            context=format_entities(state.get("entities") or ExtractedEntities()),
        )
    logger.info(
        "[REDUCE] evidence_chars=%s references=%s map_reduced_blocks=%s",
        {agent: len(text) for agent, text in evidence.items()}, len(reduced.references), reductions,
    )
    return {
        "evidence": evidence,
        "citation_references": reduced.references,
        "evidence_map_reductions": reductions,
        "node_trace": [f"{NODE_REDUCE_OUTPUTS}:{len(reduced.references)}"],
    }


def _objectives(state: GraphState) -> dict[str, str]:
    """Each agent's latest objective, the goal its evidence is consolidated for."""
    plan = state.get("plan")
    return {step.agent: step.objective for stage in (plan.stages if plan else []) for step in stage.steps}


_VERDICT_STEP_TEXT = {
    VERDICT_SUFFICIENT: "evidence is complete",
    VERDICT_PARTIAL: "evidence checked, some information is unavailable",
    VERDICT_REPLAN: "more information needed, fetching it",
    VERDICT_ASK_HUMAN: "more details needed from you",
}


def describe_sufficiency(update: dict[str, Any]) -> str:
    verdict = update["sufficiency"].verdict
    return f"{STEP_SUFFICIENCY_NAME}: {_VERDICT_STEP_TEXT.get(verdict, 'evidence checked')}"


def describe_final(update: dict[str, Any]) -> str:
    if update.get("outcome") == OUTCOME_DEGRADED:
        return f"{STEP_FINAL_AGENT_NAME}: a reliable answer could not be written"
    cited = len(update.get("citations") or [])
    return f"{STEP_FINAL_AGENT_NAME}: response ready" + (
        f" with {cited} reference{'s' if cited != 1 else ''}" if cited else ""
    )


def make_sufficiency_node(agent: SufficiencyAgent) -> NodeFn:
    @traced_node(NODE_SUFFICIENCY, describe=describe_sufficiency)
    async def sufficiency_node(state: GraphState) -> dict[str, Any]:
        verdict = await agent.check(state)  # the agent logs and records the verdict (or that it is unverified)
        update: dict[str, Any] = {"sufficiency": verdict, "node_trace": [f"{NODE_SUFFICIENCY}:{verdict.verdict}"]}
        if verdict.verdict == VERDICT_REPLAN:
            update["refetch_history"] = verdict.refetch_instructions
        return update

    return sufficiency_node


def make_final_node(agent: FinalAgent) -> NodeFn:
    @traced_node(NODE_FINAL_AGENT, describe=describe_final)
    async def final_node(state: GraphState) -> dict[str, Any]:
        answer = await agent.write(state)
        return {
            "final_output": answer.markdown,
            "citations": [citation.model_dump() for citation in answer.citations],
            "outcome": OUTCOME_DEGRADED if answer.degraded else OUTCOME_ANSWER,
            "node_trace": [NODE_FINAL_AGENT],
        }

    return final_node
