"""Graph state: the single source of truth that flows through every node."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from app.schemas.agent_io import AgentResult, AgentTask
from app.schemas.orchestrator import (
    ExecutionPlan,
    ExtractedEntities,
    OrchestratorDecision,
    PendingClarification,
)
from app.schemas.sufficiency import RefetchInstruction, SufficiencyVerdict


def merge_agent_results(left: dict[str, AgentResult] | None, right: dict[str, AgentResult] | None) -> dict[str, AgentResult]:
    """Parallel agents write to different keys; a re-run overwrites its own earlier result."""
    return {**(left or {}), **(right or {})}


def append_trace(left: list[str] | None, right: list[str] | None) -> list[str]:
    return [*(left or []), *(right or [])]


def append_refetch_history(
    left: list[RefetchInstruction] | None, right: list[RefetchInstruction] | None
) -> list[RefetchInstruction]:
    return [*(left or []), *(right or [])]


class AgentNodeInput(TypedDict):
    """Payload sent to an agent node when a stage fans out."""

    task: AgentTask


class GraphState(TypedDict, total=False):
    # Request
    session_id: str
    user_message: str
    history_block: str
    metadata: dict[str, Any]
    pending_clarification: PendingClarification | None

    # Orchestrator
    phase: str
    decision: OrchestratorDecision | None
    resolved_query: str
    entities: ExtractedEntities
    plan: ExecutionPlan | None
    current_stage: int

    # Execution
    agent_results: Annotated[dict[str, AgentResult], merge_agent_results]
    evidence: dict[str, str]
    citation_references: list[dict[str, Any]]

    # Sufficiency loop
    sufficiency: SufficiencyVerdict | None
    replan_count: int
    refetch_history: Annotated[list[RefetchInstruction], append_refetch_history]

    # Output
    final_output: str
    citations: list[dict[str, Any]]
    outcome: str
    new_pending_clarification: PendingClarification | None
    node_trace: Annotated[list[str], append_trace]
