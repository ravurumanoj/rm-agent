"""Schemas for the sufficiency / grounding check and its structured feedback to the orchestrator."""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, Field, model_validator

from app.constants import (
    VERDICT_ASK_HUMAN,
    VERDICT_PARTIAL,
    VERDICT_REPLAN,
    VERDICT_SUFFICIENT,
)
from app.schemas.orchestrator import AgentId

VerdictType = Literal["sufficient", "replan", "ask_human", "partial"]

assert set(get_args(VerdictType)) == {VERDICT_SUFFICIENT, VERDICT_REPLAN, VERDICT_ASK_HUMAN, VERDICT_PARTIAL}


class AgentAssessment(BaseModel):
    """Judgement of one agent's output."""

    agent: AgentId = Field(..., description="Agent being assessed.")
    adequate: bool = Field(..., description="True if the output is enough for its part of the question.")
    issues: list[str] = Field(default_factory=list, description="Concrete problems: empty, off-topic, error, ungrounded.")


class RefetchInstruction(BaseModel):
    """Structured instruction telling the orchestrator what to re-fetch and from whom."""

    agent: AgentId = Field(..., description="Agent that must run again.")
    missing_information: str = Field(..., description="Exactly which information is missing or unusable.")
    instruction: str = Field(..., description="Concrete guidance for the agent on how to obtain it.")
    suggested_tools: list[str] = Field(
        default_factory=list,
        description="Names of tools worth calling, only if known from the agent's previous tool calls.",
    )


class HumanQuestion(BaseModel):
    """What the RM must supply before the answer can be completed."""

    question: str = Field(..., description="One concise question for the RM.")
    missing_fields: list[str] = Field(default_factory=list, description="Fields needed, e.g. 'portfolio_id'.")
    reason: str = Field(default="", description="Why the information is needed.")


class SufficiencyVerdict(BaseModel):
    """Outcome of the multi-purpose sufficiency check."""

    verdict: VerdictType = Field(
        ...,
        description=(
            "sufficient: answer can be written. replan: re-run named agents. "
            "ask_human: RM input is required. partial: answer with disclosed gaps, no further retrieval helps."
        ),
    )
    reasoning: str = Field(..., description="Why this verdict was chosen.")
    assessments: list[AgentAssessment] = Field(default_factory=list, description="Per-agent assessment.")
    grounding_issues: list[str] = Field(
        default_factory=list,
        description="Claims in agent summaries not supported by tool evidence, or conflicts between agents.",
    )
    refetch_instructions: list[RefetchInstruction] = Field(
        default_factory=list,
        description="Required when verdict is replan.",
    )
    human_question: HumanQuestion | None = Field(default=None, description="Required when verdict is ask_human.")
    data_gaps: list[str] = Field(
        default_factory=list,
        description="Information the final answer must state as unavailable. Used when verdict is partial.",
    )

    @model_validator(mode="after")
    def _require_fields_for_verdict(self) -> "SufficiencyVerdict":
        if self.verdict == VERDICT_REPLAN and not self.refetch_instructions:
            raise ValueError("refetch_instructions is required when verdict is replan")
        if self.verdict == VERDICT_ASK_HUMAN and self.human_question is None:
            raise ValueError("human_question is required when verdict is ask_human")
        return self
