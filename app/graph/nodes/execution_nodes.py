"""Execution nodes: stage dispatch, the three agent nodes and the stage join."""

from __future__ import annotations

from typing import Any, Protocol

from app.constants import NODE_STAGE_DISPATCH, NODE_STAGE_JOIN
from app.graph.nodes.common import NodeFn, traced_node
from app.graph.state import AgentNodeInput, GraphState
from app.schemas.agent_io import AgentResult, AgentTask
from app.utils.logger import logger


class SupportsRun(Protocol):
    agent_id: str

    async def run(self, task: AgentTask) -> AgentResult: ...


@traced_node(NODE_STAGE_DISPATCH)
async def stage_dispatch_node(state: GraphState) -> dict[str, Any]:
    """Marks a stage boundary; the fan-out to agent nodes happens on the outgoing conditional edge."""
    stage = state.get("current_stage", 0)
    plan = state.get("plan")
    agents = [step.agent for step in plan.stages[stage].steps] if plan and stage < len(plan.stages) else []
    logger.info(
        "[GRAPH] dispatch stage=%s/%s mode=%s agents=%s",
        stage + 1, len(plan.stages) if plan else 0, plan.mode if plan else "-", agents,
    )
    return {"node_trace": [f"{NODE_STAGE_DISPATCH}:{stage + 1}"]}


def make_agent_node(agent: SupportsRun, node_name: str) -> NodeFn:
    @traced_node(node_name)
    async def agent_node(payload: AgentNodeInput) -> dict[str, Any]:
        # The agent logs its own run (objective, tool calls, status); this node only records the result.
        result = await agent.run(payload["task"])
        return {
            "agent_results": {result.agent: result},
            "node_trace": [f"{node_name}:{result.status}"],
        }

    return agent_node


@traced_node(NODE_STAGE_JOIN)
async def stage_join_node(state: GraphState) -> dict[str, Any]:
    """Runs once all agents of the stage have returned, then advances to the next stage."""
    next_stage = state.get("current_stage", 0) + 1
    return {"current_stage": next_stage, "node_trace": [f"{NODE_STAGE_JOIN}:{next_stage}"]}
