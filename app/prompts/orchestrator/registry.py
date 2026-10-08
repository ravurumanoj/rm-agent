"""Phase-to-prompt registry: the orchestrator picks its prompt from the phase it is in."""

from __future__ import annotations

from dataclasses import dataclass

from app.constants import (
    PHASE_CLARIFICATION_FOLLOWUP,
    PHASE_HUMAN_IN_THE_LOOP,
    PHASE_REPLAN,
    PHASE_ROUTING,
)
from app.prompts.orchestrator.clarification_followup import (
    CLARIFICATION_FOLLOWUP_SYSTEM_PROMPT,
    CLARIFICATION_FOLLOWUP_USER_TEMPLATE,
)
from app.prompts.orchestrator.human_in_the_loop import (
    HUMAN_IN_THE_LOOP_SYSTEM_PROMPT,
    HUMAN_IN_THE_LOOP_USER_TEMPLATE,
)
from app.prompts.orchestrator.replan import REPLAN_SYSTEM_PROMPT, REPLAN_USER_TEMPLATE
from app.prompts.orchestrator.routing import ROUTING_SYSTEM_PROMPT, ROUTING_USER_TEMPLATE


@dataclass(frozen=True)
class PromptTemplate:
    system: str
    user: str

    def render(self, variables: dict[str, str]) -> tuple[str, str]:
        return self.system.format_map(variables), self.user.format_map(variables)


_ORCHESTRATOR_PROMPTS: dict[str, PromptTemplate] = {
    PHASE_ROUTING: PromptTemplate(ROUTING_SYSTEM_PROMPT, ROUTING_USER_TEMPLATE),
    PHASE_CLARIFICATION_FOLLOWUP: PromptTemplate(
        CLARIFICATION_FOLLOWUP_SYSTEM_PROMPT, CLARIFICATION_FOLLOWUP_USER_TEMPLATE
    ),
    PHASE_REPLAN: PromptTemplate(REPLAN_SYSTEM_PROMPT, REPLAN_USER_TEMPLATE),
    PHASE_HUMAN_IN_THE_LOOP: PromptTemplate(HUMAN_IN_THE_LOOP_SYSTEM_PROMPT, HUMAN_IN_THE_LOOP_USER_TEMPLATE),
}


def get_orchestrator_prompt(phase: str) -> PromptTemplate:
    try:
        return _ORCHESTRATOR_PROMPTS[phase]
    except KeyError as exc:
        raise ValueError(f"No orchestrator prompt registered for phase '{phase}'") from exc
