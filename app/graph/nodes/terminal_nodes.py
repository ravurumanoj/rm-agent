"""Terminal nodes that end a turn without running agents: direct reply, safe decline, ask the RM.

All text comes from the orchestrator decision, which the LLM wrote for the current phase.
"""

from __future__ import annotations

from typing import Any

from app.constants import (
    NODE_ASK_HUMAN,
    NODE_DIRECT_REPLY,
    NODE_SAFE_DECLINE,
    OUTCOME_CLARIFICATION,
    OUTCOME_DECLINED,
    OUTCOME_GREETING,
)
from app.graph.nodes.common import traced_node
from app.graph.state import GraphState
from app.schemas.orchestrator import ExtractedEntities, PendingClarification
from app.utils.logger import logger, preview


def _reply(state: GraphState, node: str, outcome: str) -> dict[str, Any]:
    reply = state["decision"].direct_reply
    logger.info("[GRAPH] terminal_reply node=%s outcome=%s reply=%s", node, outcome, preview(reply))
    return {"final_output": reply, "citations": [], "outcome": outcome, "node_trace": [node]}


@traced_node(NODE_DIRECT_REPLY)
async def direct_reply_node(state: GraphState) -> dict[str, Any]:
    return _reply(state, NODE_DIRECT_REPLY, OUTCOME_GREETING)


@traced_node(NODE_SAFE_DECLINE)
async def safe_decline_node(state: GraphState) -> dict[str, Any]:
    return _reply(state, NODE_SAFE_DECLINE, OUTCOME_DECLINED)


@traced_node(NODE_ASK_HUMAN)
async def ask_human_node(state: GraphState) -> dict[str, Any]:
    """Human-in-the-loop exit: the question is the reply; the next turn resumes from the pending record."""
    question = state["decision"].clarification_question
    logger.info("[GRAPH] ask_human question=%s", preview(question))
    pending = PendingClarification(
        original_query=state.get("resolved_query") or state.get("user_message", ""),
        question=question,
        entities=state.get("entities") or ExtractedEntities(),
    )
    return {
        "final_output": question,
        "citations": [],
        "outcome": OUTCOME_CLARIFICATION,
        "new_pending_clarification": pending,
        "node_trace": [NODE_ASK_HUMAN],
    }
