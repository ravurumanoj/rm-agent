"""Data models and config of the ReAct component."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field

TERMINATED_FINAL_ANSWER = "final_answer"
TERMINATED_MAX_ITERATIONS = "max_iterations"
TERMINATED_REPEATED_CALLS = "repeated_calls"
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


_EMPTY_CONTENT = frozenset({"", "[]", "{}", "null", "none"})


def is_empty_output(output: ToolOutput | None) -> bool:
    return output is not None and not output.sources and output.content.strip().lower() in _EMPTY_CONTENT


class ToolCallRecord(BaseModel):
    """Audit record of one tool call."""

    iteration: int = Field(..., description="ReAct iteration in which the call was issued.")
    tool_name: str = Field(..., description="Name of the tool that was called.")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Arguments the model supplied.")
    output: ToolOutput | None = Field(default=None, description="Output when the call succeeded.")
    error: str | None = Field(default=None, description="Error message when the call failed.")
    error_kind: str | None = Field(
        default=None,
        description="retryable (temporarily unavailable), non_retryable (rejected) or circuit_open (skipped).",
    )
    attempts: int = Field(default=1, description="Attempts made, including retries.")
    reduced_from_tokens: int | None = Field(
        default=None, description="Estimated size before map-reduce consolidated the output; None if untouched."
    )
    map_reduced: bool = Field(default=False, description="True when the output was consolidated by LLM map-reduce.")
    duration_ms: int = Field(default=0, description="Wall-clock duration of the call.")

    @property
    def succeeded(self) -> bool:
        return self.error is None and self.output is not None

    @property
    def no_data(self) -> bool:
        """The call worked but the source had nothing to return: a valid answer, not a failure."""
        return self.succeeded and is_empty_output(self.output)


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
    # Call-level retries of retryable tool errors (0 = no retry) and the server-level circuit breaker.
    max_retries: int = 0
    retry_base_delay: float = 1.0
    retry_max_delay: float = 8.0
    retry_jitter_ratio: float = 0.25
    retry_on_timeout: bool = True
    breaker_key: str = ""  # one key per tool server; empty disables the breaker
    breaker_failure_threshold: int = 5
    breaker_cooldown_seconds: float = 60.0
    # Optional tracing hooks, so the component needs no tracing library: tool_span(name, arguments) returns a
    # context manager around one tool call; on_tool_result(span, record) records its outcome on that span.
    tool_span: Callable[[str, dict[str, Any]], AbstractContextManager[Any]] | None = None
    on_tool_result: Callable[[Any, "ToolCallRecord"], None] | None = None
    # Optional hook to shrink large tool results before they enter the conversation:
    # reduce_observations(executed_calls, conversation_chars) -> executed_calls (same order).
    reduce_observations: Callable[[list[Any], int], Awaitable[list[Any]]] | None = None


@dataclass
class ReActOutcome:
    final_text: str = ""
    records: list[ToolCallRecord] = field(default_factory=list)
    steps: list[ReActStep] = field(default_factory=list)
    iterations: int = 0
    terminated_reason: str = TERMINATED_FINAL_ANSWER
    error: str | None = None
