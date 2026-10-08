"""Schemas produced by the orchestrator: routing decision, execution plan and clarification state."""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, Field, computed_field, model_validator

from app.constants import (
    EXEC_MODE_NONE,
    EXEC_MODE_PARALLEL,
    EXEC_MODE_SEQUENTIAL,
    EXEC_MODE_SINGLE,
    INTENT_DATA_REQUEST,
    INTENT_GREETING,
    INTENT_NEEDS_CLARIFICATION,
    INTENT_OUT_OF_SCOPE,
    VALID_AGENTS,
)

AgentId = Literal["portfolio", "crm", "admin"]
TurnIntent = Literal["greeting", "out_of_scope", "data_request", "needs_clarification"]

assert set(get_args(AgentId)) == VALID_AGENTS, "AgentId must stay in sync with constants.VALID_AGENTS"


class ExtractedEntities(BaseModel):
    """Entities the orchestrator pulled out of the conversation to scope the agents."""

    client_id: str | None = Field(default=None, description="Client identifier if stated or known from context.")
    portfolio_ids: list[str] = Field(
        default_factory=list,
        description="Portfolio identifiers mentioned by the RM or active in context. Empty if none.",
    )
    time_range: str | None = Field(
        default=None,
        description="Time window the RM cares about, e.g. 'last quarter', 'YTD'. Null if not stated.",
    )
    topics: list[str] = Field(
        default_factory=list,
        description="Key topics or concerns in the request, e.g. 'underperformance', 'rebalancing'.",
    )


class PlanStep(BaseModel):
    """One unit of work assigned to one agent."""

    agent: AgentId = Field(..., description="Agent that executes this step.")
    objective: str = Field(
        ...,
        description="Self-contained instruction for the agent: what to retrieve and why, scoped to the query.",
    )
    extra_instruction: str = Field(
        default="",
        description="Additional guidance, e.g. a re-fetch instruction from the sufficiency check. Empty on first pass.",
    )


class ExecutionStage(BaseModel):
    """Steps in one stage run concurrently; stages run in order."""

    steps: list[PlanStep] = Field(..., min_length=1, description="Independent steps to run in parallel.")


class ExecutionPlan(BaseModel):
    """Ordered stages. One stage with one step is 'single', one stage with many is 'parallel', many stages is 'sequential'."""

    stages: list[ExecutionStage] = Field(
        default_factory=list,
        description=(
            "Ordered stages. Put agents whose data is independent in the same stage. "
            "Put an agent in a later stage only if it needs the output of an earlier stage."
        ),
    )

    @model_validator(mode="after")
    def _dedupe_agents(self) -> "ExecutionPlan":
        seen: set[str] = set()
        cleaned: list[ExecutionStage] = []
        for stage in self.stages:
            steps = []
            for step in stage.steps:
                if step.agent in seen:
                    continue
                seen.add(step.agent)
                steps.append(step)
            if steps:
                cleaned.append(ExecutionStage(steps=steps))
        self.stages = cleaned
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mode(self) -> str:
        if not self.stages:
            return EXEC_MODE_NONE
        if len(self.stages) > 1:
            return EXEC_MODE_SEQUENTIAL
        return EXEC_MODE_SINGLE if len(self.stages[0].steps) == 1 else EXEC_MODE_PARALLEL

    @property
    def agents(self) -> list[str]:
        return [step.agent for stage in self.stages for step in stage.steps]

    @property
    def is_empty(self) -> bool:
        return not self.stages


class OrchestratorDecision(BaseModel):
    """Single decision schema returned by the orchestrator in every phase."""

    reasoning: str = Field(
        ...,
        description="Brief justification of the decision: intent, which agents, and why this mode.",
    )
    intent: TurnIntent = Field(
        ...,
        description=(
            "greeting: small talk. out_of_scope: unrelated to RM work. "
            "data_request: needs agent data. needs_clarification: mandatory information is missing."
        ),
    )
    resolved_query: str = Field(
        default="",
        description="The RM's request rewritten as a standalone question using conversation context.",
    )
    entities: ExtractedEntities = Field(default_factory=ExtractedEntities, description="Entities extracted for scoping.")
    plan: ExecutionPlan | None = Field(
        default=None,
        description="Execution plan. Required when intent is data_request, otherwise null.",
    )
    direct_reply: str | None = Field(
        default=None,
        description=(
            "Required for greeting and out_of_scope. greeting: a short friendly reply. out_of_scope: a polite "
            "decline that says what you can help with. Null otherwise."
        ),
    )
    clarification_question: str | None = Field(
        default=None,
        description="Required for needs_clarification: one concise question for the RM. Null otherwise.",
    )

    @model_validator(mode="after")
    def _require_fields_for_intent(self) -> "OrchestratorDecision":
        if self.intent in (INTENT_GREETING, INTENT_OUT_OF_SCOPE) and not (self.direct_reply or "").strip():
            raise ValueError(f"direct_reply is required when intent is {self.intent}")
        if self.intent == INTENT_NEEDS_CLARIFICATION and not (self.clarification_question or "").strip():
            raise ValueError("clarification_question is required when intent is needs_clarification")
        if self.intent == INTENT_DATA_REQUEST:
            if self.plan is None or self.plan.is_empty:
                raise ValueError("plan with at least one step is required when intent is data_request")
            if not self.resolved_query.strip():
                raise ValueError("resolved_query is required when intent is data_request")
        return self


class PendingClarification(BaseModel):
    """Remembered between turns when the assistant asked the RM a question."""

    original_query: str = Field(..., description="The RM request that triggered the clarification.")
    question: str = Field(..., description="The question that was put to the RM.")
    entities: ExtractedEntities = Field(
        default_factory=ExtractedEntities,
        description="Entities already known when the question was asked.",
    )
