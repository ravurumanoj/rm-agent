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

    agent: AgentId = Field(..., description="Agent being assessed; only agents that ran.")
    adequate: bool = Field(
        ...,
        description="True only if its part of the request is complete, relevant, grounded and has its required fields.",
    )
    issues: list[str] = Field(
        default_factory=list,
        description=(
            "Concrete problems, each naming what is affected: missing item or field, wrong scope, failed call, "
            "ungrounded or uncited claim."
        ),
    )


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

    question: str = Field(..., description="One concise question for the RM that names what is already known.")
    missing_fields: list[str] = Field(default_factory=list, description="Fields needed, e.g. 'portfolio_id'.")
    reason: str = Field(default="", description="Why the information is needed.")


class SufficiencyVerdict(BaseModel):
    """Outcome of the multi-purpose sufficiency check. Findings come first so the verdict follows from them."""

    reasoning: str = Field(..., description="Brief: what the checks found and why the verdict follows.")
    assessments: list[AgentAssessment] = Field(default_factory=list, description="One assessment per agent that ran.")
    grounding_issues: list[str] = Field(
        default_factory=list,
        description="Claims not backed by tool evidence, or values that must be cited but have no source marker.",
    )
    conflicts: list[str] = Field(
        default_factory=list,
        description="Facts on which sources disagree, each with both values and both markers. Never resolved here.",
    )
    guardrail_issues: list[str] = Field(
        default_factory=list,
        description=(
            "Evidence the final answer must not use or must label, each with the reason: advice or recommendations, "
            "compliance decisions, chat or in-person meetings, out-of-scope or non-entitled records, outside "
            "information, unlabelled calculated content."
        ),
    )
    verdict: VerdictType = Field(
        ...,
        description=(
            "ask_human: only the RM can supply what is missing. replan: another bounded retrieval round can fix the "
            "gaps. partial: answer with the gaps disclosed; retrieving again cannot help. sufficient: every part "
            "of the request is covered by grounded, cited evidence."
        ),
    )
    refetch_instructions: list[RefetchInstruction] = Field(
        default_factory=list,
        description="Required when verdict is replan: one entry per agent to re-run.",
    )
    human_question: HumanQuestion | None = Field(default=None, description="Required when verdict is ask_human.")
    data_gaps: list[str] = Field(
        default_factory=list,
        description=(
            "What the RM asked for that is unavailable and why, in plain words. The final answer states each gap "
            "and shows no section for it. Required when verdict is partial."
        ),
    )

    @model_validator(mode="after")
    def _require_fields_for_verdict(self) -> "SufficiencyVerdict":
        if self.verdict == VERDICT_REPLAN and not self.refetch_instructions:
            raise ValueError("refetch_instructions is required when verdict is replan")
        if self.verdict == VERDICT_ASK_HUMAN and self.human_question is None:
            raise ValueError("human_question is required when verdict is ask_human")
        if self.verdict == VERDICT_PARTIAL and not self.data_gaps:
            raise ValueError("data_gaps is required when verdict is partial")
        return self
