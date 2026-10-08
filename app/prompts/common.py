"""Prompt fragments shared by several agents."""

from __future__ import annotations

from app.constants import AGENT_CAPABILITIES, AGENT_DISPLAY_NAMES, AGENT_ORDER


def format_agent_catalog() -> str:
    """List the available agents with their capabilities, generated from constants."""
    return "\n".join(
        f'- "{agent}" ({AGENT_DISPLAY_NAMES[agent]}): {AGENT_CAPABILITIES[agent]}' for agent in AGENT_ORDER
    )


PLAN_BUILDING_RULES = """\
Plan rules:
- A plan is an ordered list of stages. Steps inside one stage run in parallel.
- Put agents in the same stage when their data is independent.
- Use a later stage only when an agent needs the output of an earlier stage, for example: find the client's \
concern with "crm", then check whether it relates to underperformance with "portfolio", then look up the policy \
or process with "admin".
- Use each agent at most once and use the fewest agents that can fully answer the request.
- Write each objective as a self-contained instruction: what to retrieve, for which client or portfolio, and over \
which period. Do not assume the agent can see the conversation."""

GROUNDING_RULES = """\
Grounding rules:
- Use only facts present in the provided evidence. Never invent numbers, dates, names or quotes.
- If the evidence does not cover something, say it is unavailable instead of guessing."""
