"""Orchestrator node: runs the phase-adaptive decision and writes it into the state."""

from __future__ import annotations

from typing import Any

from app.agents.orchestrator.agent import OrchestratorAgent
from app.constants import INTENT_DATA_REQUEST, NODE_ORCHESTRATOR, PHASE_REPLAN
from app.graph.nodes.common import NodeFn, traced_node
from app.graph.state import GraphState
from app.utils.logger import logger, preview


def make_orchestrator_node(agent: OrchestratorAgent) -> NodeFn:
    @traced_node(NODE_ORCHESTRATOR)
    async def orchestrator_node(state: GraphState) -> dict[str, Any]:
        phase, decision = await agent.decide(state)
        logger.info(
            "[ORCH] decision phase=%s intent=%s mode=%s agents=%s",
            phase,
            decision.intent,
            decision.plan.mode if decision.plan else "-",
            decision.plan.agents if decision.plan else [],
        )
        logger.debug(
            "[ORCH] resolved_query=%s entities=%s direct_reply=%s clarification=%s",
            preview(decision.resolved_query), preview(decision.entities),
            preview(decision.direct_reply), preview(decision.clarification_question),
        )
        update: dict[str, Any] = {
            "phase": phase,
            "decision": decision,
            "resolved_query": decision.resolved_query,
            "entities": decision.entities,
            "node_trace": [f"{NODE_ORCHESTRATOR}:{phase}:{decision.intent}"],
        }
        if decision.intent == INTENT_DATA_REQUEST:
            update.update(plan=decision.plan, current_stage=0)
        if phase == PHASE_REPLAN:
            update["replan_count"] = state.get("replan_count", 0) + 1
        return update

    return orchestrator_node
