"""Multi-purpose sufficiency check: coverage, grounding, re-fetch instructions and questions for the RM."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import BaseAgent
from app.agents.formatting import format_entities, format_plan, format_refetch_instructions
from app.components.structured_output import invoke_structured, schema_instructions
from app.config import settings
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
        logger.info(
            "[SUFF] checking replan_count=%s evidence_agents=%s",
            state.get("replan_count", 0), list((state.get("evidence") or {}).keys()),
        )
        logger.debug("[SUFF] user_prompt=%s", preview(user_prompt))
        return await invoke_structured(
            self.llm,
            [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)],
            SufficiencyVerdict,
            label="sufficiency",
            max_attempts=settings.AGENT_STRUCTURED_OUTPUT_ATTEMPTS,
        )
