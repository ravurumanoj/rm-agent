"""Data models and config of the ReAct component."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

TERMINATED_FINAL_ANSWER = "final_answer"
TERMINATED_MAX_ITERATIONS = "max_iterations"
TERMINATED_LLM_ERROR = "llm_error"


class SourceItem(BaseModel):
    """One citable source behind a tool result."""

    source_id: str = Field(..., description="Stable unique identifier of the underlying record.")
    title: str = Field(default="", description="Human-readable title shown in citations.")
    uri: str = Field(default="", description="Link or locator of the source, when one exists.")
    snippet: str = Field(default="", description="Short excerpt used as the citation preview.")
    source: str = Field(default="", description="Origin label of the source, kept as-is when the tool provides one.")


class ToolOutput(BaseModel):
    """Normalized result every tool or external agent returns."""

    content: str = Field(..., description="Plain-text result the agent reasons over.")
    sources: list[SourceItem] = Field(default_factory=list, description="Sources backing the content.")


class ToolCallRecord(BaseModel):
    """Audit record of one tool call."""

    iteration: int = Field(..., description="ReAct iteration in which the call was issued.")
    tool_name: str = Field(..., description="Name of the tool that was called.")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Arguments the model supplied.")
    output: ToolOutput | None = Field(default=None, description="Output when the call succeeded.")
    error: str | None = Field(default=None, description="Error message when the call failed.")
    duration_ms: int = Field(default=0, description="Wall-clock duration of the call.")

    @property
    def succeeded(self) -> bool:
        return self.error is None and self.output is not None


class ReActStep(BaseModel):
    """One reason-then-act cycle."""

    iteration: int = Field(..., description="1-based iteration number.")
    thought: str = Field(default="", description="Model reasoning text emitted before acting.")
    tool_names: list[str] = Field(default_factory=list, description="Tools chosen in this step.")


@dataclass
class ReActConfig:
    max_iterations: int = 5
    tool_timeout_seconds: float = 30.0
    max_parallel_tool_calls: int = 8
    force_final_message: str = "Stop calling tools and write your final answer now from the observations so far."


@dataclass
class ReActOutcome:
    final_text: str = ""
    records: list[ToolCallRecord] = field(default_factory=list)
    steps: list[ReActStep] = field(default_factory=list)
    iterations: int = 0
    terminated_reason: str = TERMINATED_FINAL_ANSWER
    error: str | None = None
