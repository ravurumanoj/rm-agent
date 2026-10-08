"""Input and output contracts shared by all sub-agents."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.components.react.models import ReActStep, SourceItem, ToolCallRecord
from app.constants import AGENT_STATUS_OK
from app.schemas.orchestrator import AgentId, ExtractedEntities


class AgentTask(BaseModel):
    """Work order the orchestrator hands to a sub-agent."""

    agent: AgentId = Field(..., description="Agent that must execute the task.")
    objective: str = Field(..., description="What the agent must retrieve or answer.")
    user_query: str = Field(..., description="The RM's standalone question, for overall context.")
    entities: ExtractedEntities = Field(default_factory=ExtractedEntities, description="Scoping entities.")
    handoff_context: str = Field(
        default="",
        description="Findings from other agents the task may build on. Empty when nothing precedes this step.",
    )
    extra_instruction: str = Field(default="", description="Re-fetch or refinement guidance. Empty on first pass.")
    attempt: int = Field(default=1, ge=1, description="1 for the first pass, incremented on each re-fetch.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Request metadata such as client_id.")


class AgentResult(BaseModel):
    """Result every sub-agent returns to the graph."""

    agent: AgentId = Field(..., description="Agent that produced the result.")
    attempt: int = Field(default=1, description="Attempt number this result belongs to.")
    status: str = Field(
        default=AGENT_STATUS_OK,
        description="ok: all calls succeeded. partial: some failed. no_data: nothing retrieved. error: failed.",
    )
    summary: str = Field(default="", description="Agent's own merged findings, grounded in its tool outputs.")
    tool_calls: list[ToolCallRecord] = Field(default_factory=list, description="Every tool call that was made.")
    steps: list[ReActStep] = Field(default_factory=list, description="ReAct reasoning trace; empty for non-ReAct agents.")
    sources: list[SourceItem] = Field(default_factory=list, description="De-duplicated sources from successful calls.")
    iterations: int = Field(default=0, description="Number of LLM reasoning iterations used.")
    terminated_reason: str = Field(
        default="",
        description="Why the loop ended: final_answer, max_iterations, llm_error or single_call.",
    )
    error: str | None = Field(default=None, description="Agent-level error, when the agent itself failed.")
