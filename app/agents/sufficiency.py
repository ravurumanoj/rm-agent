"""Multi-purpose sufficiency check: coverage, grounding, re-fetch instructions and questions for the RM."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import BaseAgent
from app.agents.formatting import format_entities, format_plan, format_refetch_instructions
from app.components.structured_output import invoke_structured, schema_instructions
from app.config import settings
from app.constants import VERDICT_PARTIAL, VERDICT_REPLAN, VERDICT_SUFFICIENT
from app.graph.state import GraphState
from app.prompts.sufficiency import SUFFICIENCY_SYSTEM_PROMPT, SUFFICIENCY_USER_TEMPLATE
from app.schemas.orchestrator import ExtractedEntities
from app.schemas.sufficiency import SufficiencyVerdict
from app.utils.logger import logger, preview


class SufficiencyAgent(BaseAgent):
    async def check(self, state: GraphState) -> SufficiencyVerdict:
        system_prompt = SUFFICIENCY_SYSTEM_PROMPT.format_map(
            {
                "replan_count": state.get("replan_count", 0),
                "max_replans": settings.AGENT_MAX_REPLAN_LOOPS,
                "output_schema": schema_instructions(SufficiencyVerdict),
            }
        )
        user_prompt = SUFFICIENCY_USER_TEMPLATE.format_map(
            {
                "resolved_query": state.get("resolved_query", ""),
                "known_entities": format_entities(state.get("entities") or ExtractedEntities()),
                "plan_overview": format_plan(state.get("plan")),
                "refetch_history": format_refetch_instructions(state.get("refetch_history") or []),
                "evidence": "\n\n".join((state.get("evidence") or {}).values()) or "(no agent output)",
            }
        )
        logger.debug("[SUFF] user_prompt=%s", preview(user_prompt))
        try:
            verdict = await invoke_structured(
                self.llm,
                [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)],
                SufficiencyVerdict,
                label="sufficiency",
                max_attempts=settings.AGENT_STRUCTURED_OUTPUT_ATTEMPTS,
            )
        except Exception:
            # Degrade instead of failing the turn: the final agent still answers only from the evidence.
            logger.warning(
                "[SUFF] check_failed replans=%s evidence_agents=%s; passing the evidence on unverified",
                state.get("replan_count", 0), list((state.get("evidence") or {}).keys()), exc_info=True,
            )
            return unverified_verdict()
        verdict = enforce_replan_budget(verdict, state.get("replan_count", 0))
        logger.info(
            "[SUFF] verdict=%s replans=%s/%s inadequate=%s grounding_issues=%s conflicts=%s guardrail_issues=%s "
            "refetch=%s data_gaps=%s ask_rm=%s reasoning=%s",
            verdict.verdict, state.get("replan_count", 0), settings.AGENT_MAX_REPLAN_LOOPS,
            [item.agent for item in verdict.assessments if not item.adequate], len(verdict.grounding_issues),
            len(verdict.conflicts), len(verdict.guardrail_issues),
            [f"{item.agent}: {item.missing_information}" for item in verdict.refetch_instructions],
            len(verdict.data_gaps), bool(verdict.human_question), preview(verdict.reasoning),
        )
        return verdict


def unverified_verdict() -> SufficiencyVerdict:
    """Safe default when the check itself cannot run: no replan, no question, evidence flagged as unchecked."""
    return SufficiencyVerdict(
        verdict=VERDICT_SUFFICIENT,
        reasoning=(
            "The evidence check could not run, so the evidence is passed on unverified: state only what it "
            "directly supports, cite it, and say plainly what is missing."
        ),
    )


def enforce_replan_budget(verdict: SufficiencyVerdict, replan_count: int) -> SufficiencyVerdict:
    """With no re-fetch attempts left, a replan becomes partial so the final answer discloses the gaps."""
    if verdict.verdict != VERDICT_REPLAN or replan_count < settings.AGENT_MAX_REPLAN_LOOPS:
        return verdict
    logger.warning(
        "[SUFF] replan_budget_exhausted replans=%s/%s; downgrading replan to partial with %s gap(s)",
        replan_count, settings.AGENT_MAX_REPLAN_LOOPS, len(verdict.refetch_instructions) + len(verdict.data_gaps),
    )
    gaps = [*verdict.data_gaps, *(item.missing_information for item in verdict.refetch_instructions)]
    return verdict.model_copy(update={"verdict": VERDICT_PARTIAL, "data_gaps": gaps, "refetch_instructions": []})
