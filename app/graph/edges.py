"""Conditional edges: the only place where graph routing decisions are made."""

from __future__ import annotations

from langgraph.types import Send

from app.config import settings
from app.constants import (
    AGENT_TO_NODE,
    INTENT_DATA_REQUEST,
    INTENT_GREETING,
    INTENT_NEEDS_CLARIFICATION,
    INTENT_OUT_OF_SCOPE,
    INTENT_UNSAFE,
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
from app.utils.logger import logger

_INTENT_TO_NODE = {
    INTENT_DATA_REQUEST: NODE_STAGE_DISPATCH,
    INTENT_GREETING: NODE_DIRECT_REPLY,
    INTENT_NEEDS_CLARIFICATION: NODE_ASK_HUMAN,
    INTENT_OUT_OF_SCOPE: NODE_SAFE_DECLINE,
    INTENT_UNSAFE: NODE_SAFE_DECLINE,
}


def route_after_orchestrator(state: GraphState) -> str:
    intent = state["decision"].intent
    next_node = _INTENT_TO_NODE.get(intent)
    if next_node is None:
        logger.warning("[GRAPH] route from=orchestrator unknown_intent=%s next=%s (safe default)", intent, NODE_SAFE_DECLINE)
        return NODE_SAFE_DECLINE
    logger.info("[GRAPH] route from=orchestrator intent=%s next=%s", intent, next_node)
    return next_node


def fan_out_stage(state: GraphState) -> list[Send]:
    """One Send per step of the current stage; steps in a stage run concurrently."""
    tasks = build_stage_tasks(state)
    logger.info(
        "[GRAPH] fan_out stage=%s/%s agents=%s attempt=%s",
        state.get("current_stage", 0) + 1, len(state["plan"].stages), [task.agent for task in tasks],
        tasks[0].attempt if tasks else "-",
    )
    return [Send(AGENT_TO_NODE[task.agent], {"task": task}) for task in tasks]


def route_after_stage_join(state: GraphState) -> str:
    completed, total = state.get("current_stage", 0), len(state["plan"].stages)
    next_node = NODE_STAGE_DISPATCH if completed < total else NODE_REDUCE_OUTPUTS
    logger.info("[GRAPH] route from=stage_join stages_done=%s/%s next=%s", completed, total, next_node)
    return next_node


def route_after_sufficiency(state: GraphState) -> str:
    """Loop back for a replan only while budget remains; the final agent discloses what is still missing."""
    verdict = state["sufficiency"].verdict
    replan_count = state.get("replan_count", 0)
    replan_allowed = replan_count < settings.AGENT_MAX_REPLAN_LOOPS
    loop_back = verdict == VERDICT_ASK_HUMAN or (verdict == VERDICT_REPLAN and replan_allowed)
    next_node = NODE_ORCHESTRATOR if loop_back else NODE_FINAL_AGENT
    if verdict == VERDICT_REPLAN and not replan_allowed:
        logger.warning(
            "[GRAPH] route from=sufficiency verdict=%s replans=%s/%s next=%s (replan budget exhausted; answering with gaps)",
            verdict, replan_count, settings.AGENT_MAX_REPLAN_LOOPS, next_node,
        )
    else:
        logger.info(
            "[GRAPH] route from=sufficiency verdict=%s replans=%s/%s next=%s",
            verdict, replan_count, settings.AGENT_MAX_REPLAN_LOOPS, next_node,
        )
    return next_node
