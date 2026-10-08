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
    VERDICT_REPLAN,
)
from app.graph.nodes.common import NodeFn, traced_node
from app.graph.state import GraphState
from app.services.output_reducer import reduce_agent_results
from app.utils.logger import logger, preview


@traced_node(NODE_REDUCE_OUTPUTS)
async def reduce_outputs_node(state: GraphState) -> dict[str, Any]:
    reduced = reduce_agent_results(state.get("agent_results") or {})
    logger.info(
        "[REDUCE] evidence_agents=%s references=%s evidence_chars=%s",
        list(reduced.evidence), len(reduced.references), sum(len(text) for text in reduced.evidence.values()),
    )
    logger.debug("[REDUCE] evidence=%s", preview(reduced.evidence))
    return {
        "evidence": reduced.evidence,
        "citation_references": reduced.references,
        "node_trace": [f"{NODE_REDUCE_OUTPUTS}:{len(reduced.references)}"],
    }


def make_sufficiency_node(agent: SufficiencyAgent) -> NodeFn:
    @traced_node(NODE_SUFFICIENCY)
    async def sufficiency_node(state: GraphState) -> dict[str, Any]:
        verdict = await agent.check(state)
        logger.info("[SUFF] verdict=%s reasoning=%s", verdict.verdict, preview(verdict.reasoning))
        if verdict.verdict == VERDICT_REPLAN:
            logger.info("[SUFF] refetch_instructions=%s", preview(verdict.refetch_instructions))
        update: dict[str, Any] = {"sufficiency": verdict, "node_trace": [f"{NODE_SUFFICIENCY}:{verdict.verdict}"]}
        if verdict.verdict == VERDICT_REPLAN:
            update["refetch_history"] = verdict.refetch_instructions
        return update

    return sufficiency_node


def make_final_node(agent: FinalAgent) -> NodeFn:
    @traced_node(NODE_FINAL_AGENT)
    async def final_node(state: GraphState) -> dict[str, Any]:
        answer = await agent.write(state)
        return {
            "final_output": answer.markdown,
            "citations": [citation.model_dump() for citation in answer.citations],
            "outcome": OUTCOME_ANSWER,
            "node_trace": [NODE_FINAL_AGENT],
        }

    return final_node
