"""Conditional edges: the only place where graph routing decisions are made."""

from __future__ import annotations

from langgraph.types import Send

from app.config import settings
from app.constants import (
    AGENT_TO_NODE,
    INTENT_DATA_REQUEST,
    INTENT_GREETING,
    INTENT_NEEDS_CLARIFICATION,
    NODE_ASK_HUMAN,
    NODE_DIRECT_REPLY,
    NODE_FINAL_AGENT,
    NODE_ORCHESTRATOR,
    NODE_REDUCE_OUTPUTS,
    NODE_SAFE_DECLINE,
    NODE_STAGE_DISPATCH,
    VERDICT_ASK_HUMAN,
    VERDICT_REPLAN,
)
from app.graph.state import GraphState
from app.graph.tasks import build_stage_tasks

_INTENT_TO_NODE = {
    INTENT_DATA_REQUEST: NODE_STAGE_DISPATCH,
    INTENT_GREETING: NODE_DIRECT_REPLY,
    INTENT_NEEDS_CLARIFICATION: NODE_ASK_HUMAN,
}


def route_after_orchestrator(state: GraphState) -> str:
    return _INTENT_TO_NODE.get(state["decision"].intent, NODE_SAFE_DECLINE)


def fan_out_stage(state: GraphState) -> list[Send]:
    """One Send per step of the current stage; steps in a stage run concurrently."""
    return [Send(AGENT_TO_NODE[task.agent], {"task": task}) for task in build_stage_tasks(state)]


def route_after_stage_join(state: GraphState) -> str:
    if state.get("current_stage", 0) < len(state["plan"].stages):
        return NODE_STAGE_DISPATCH
    return NODE_REDUCE_OUTPUTS


def route_after_sufficiency(state: GraphState) -> str:
    """Loop back for a replan only while budget remains; the final agent discloses what is still missing."""
    verdict = state["sufficiency"].verdict
    replan_allowed = state.get("replan_count", 0) < settings.AGENT_MAX_REPLAN_LOOPS
    if verdict == VERDICT_ASK_HUMAN or (verdict == VERDICT_REPLAN and replan_allowed):
        return NODE_ORCHESTRATOR
    return NODE_FINAL_AGENT
