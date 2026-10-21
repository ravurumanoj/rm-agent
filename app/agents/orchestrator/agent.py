"""Phase-adaptive orchestrator: one decision schema, a different prompt per phase."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import BaseAgent
from app.agents.orchestrator.context import build_orchestrator_variables
from app.agents.orchestrator.phase import fallback_decision, resolve_phase, validate_for_phase
from app.components.llm_call_context import llm_call
from app.components.structured_output import StructuredOutputError, invoke_structured
from app.config import settings
from app.graph.state import GraphState
from app.prompts.orchestrator.registry import get_orchestrator_prompt
from app.schemas.orchestrator import OrchestratorDecision
from app.utils.logger import logger, preview


class OrchestratorAgent(BaseAgent):
    async def decide(self, state: GraphState) -> tuple[str, OrchestratorDecision]:
        """Return the phase that was handled and the validated decision for it."""
        phase = resolve_phase(state)
        system_prompt, user_prompt = get_orchestrator_prompt(phase).render(build_orchestrator_variables(state))
        logger.debug(
            "[ORCH] input phase=%s user_message=%s user_prompt=%s",
            phase, preview(state.get("user_message", "")), preview(user_prompt),
        )

        try:
            with llm_call(phase):  # llm.orchestrator.routing / .replan / ...
                decision = await invoke_structured(
                    self.llm,
                    [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)],
                    OrchestratorDecision,
                    label=f"orchestrator.{phase}",
                    max_attempts=settings.AGENT_STRUCTURED_OUTPUT_ATTEMPTS,
                    post_validate=lambda decision: validate_for_phase(decision, state, phase),
                )
        except StructuredOutputError:
            # Invalid output even after repair and reprompt. An unreachable LLM still raises: that is an outage,
            # not a routing question, and the turn reports it honestly.
            decision = fallback_decision(state, phase)
            logger.warning(
                "[ORCH] invalid_output_after_repair phase=%s; safe default intent=%s agents=%s",
                phase, decision.intent, decision.plan.agents if decision.plan else [],
            )
        return phase, decision
