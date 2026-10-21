"""Orchestrator node: runs the phase-adaptive decision and writes it into the state."""

from __future__ import annotations

from typing import Any

from app.agents.orchestrator.agent import OrchestratorAgent
from app.constants import INTENT_DATA_REQUEST, NODE_ORCHESTRATOR, PHASE_REPLAN
from app.graph.nodes.common import NodeFn, traced_node
from app.graph.state import GraphState
from app.services.observability import record_routing_decision
from app.utils.logger import logger, preview


def make_orchestrator_node(agent: OrchestratorAgent) -> NodeFn:
    @traced_node(NODE_ORCHESTRATOR)
    async def orchestrator_node(state: GraphState) -> dict[str, Any]:
        phase, decision = await agent.decide(state)
        record_routing_decision(phase, decision)
        entities = decision.entities
        logger.info(
            "[ORCH] decision phase=%s intent=%s mode=%s agents=%s portfolio_ids=%s expected_count=%s client=%s "
            "resolved_query=%s reply=%s",
            phase, decision.intent, decision.plan.mode if decision.plan else "-",
            decision.plan.agents if decision.plan else [], entities.portfolio_ids,
            entities.expected_portfolio_count, entities.client_id or "-", preview(decision.resolved_query),
            preview(decision.direct_reply or decision.clarification_question or ""),
        )
        update: dict[str, Any] = {
            "phase": phase,
            "decision": decision,
            "resolved_query": decision.resolved_query,
            # Replan and human-in-the-loop prompts do not see the history, so they keep the earlier summary.
            "conversation_summary": decision.conversation_summary or state.get("conversation_summary", ""),
            "entities": decision.entities,
            "node_trace": [f"{NODE_ORCHESTRATOR}:{phase}:{decision.intent}"],
        }
        if decision.intent == INTENT_DATA_REQUEST:
            update.update(plan=decision.plan, current_stage=0)
        if phase == PHASE_REPLAN:
            update["replan_count"] = state.get("replan_count", 0) + 1
        return update

    return orchestrator_node
