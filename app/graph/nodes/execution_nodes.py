"""Execution nodes: stage dispatch, the three agent nodes and the stage join."""

from __future__ import annotations

from typing import Any, Protocol

from app.constants import (
    AGENT_ADMIN,
    AGENT_STATUS_ERROR,
    AGENT_STATUS_NO_DATA,
    AGENT_STATUS_PARTIAL,
    NODE_STAGE_DISPATCH,
    NODE_STAGE_JOIN,
    agent_step_name,
)
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


def describe_agent_result(update: dict[str, Any]) -> str:
    """Completed agent step: outcome and how many sources back it, without internal details."""
    (result,) = update["agent_results"].values()
    name = agent_step_name(result.agent)
    if result.status == AGENT_STATUS_ERROR:
        return f"{name}: could not retrieve a response"
    if result.status == AGENT_STATUS_NO_DATA:
        return f"{name}: no matching information found"
    sources = len(result.sources)
    what = "response received" if result.agent == AGENT_ADMIN else "data retrieved"
    if result.status == AGENT_STATUS_PARTIAL:
        what += " (partly)"
    return f"{name}: {what}" + (f", {sources} source{'s' if sources != 1 else ''} referenced" if sources else "")


def make_agent_node(agent: SupportsRun, node_name: str) -> NodeFn:
    @traced_node(node_name, describe=describe_agent_result)
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
