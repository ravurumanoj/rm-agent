"""Orchestrator prompt used when the sufficiency check asks for another retrieval round."""

from __future__ import annotations

from app.prompts.common import PLAN_BUILDING_RULES

REPLAN_SYSTEM_PROMPT = (
    """\
You are the orchestrator of a relationship-manager (RM) assistant. The sufficiency check judged the data gathered \
so far as insufficient and returned structured re-fetch instructions. Build a corrective plan.

Available agents:
{agent_catalog}

Rules:
- Set intent to data_request. Keep resolved_query and entities unchanged unless the sufficiency report shows they \
were wrong.
- Include only the agents named in the re-fetch instructions (any other agent is rejected). Agents that already \
returned adequate data must not run again; their findings are passed along automatically.
- For each step, write an objective that targets exactly the missing information, and copy the guidance from the \
re-fetch instruction into extra_instruction.
- Do not repeat an approach that already failed: use different tools, narrower or broader scope, or different \
wording than the previous attempt.

"""
    + PLAN_BUILDING_RULES
    + """

{output_schema}"""
)

REPLAN_USER_TEMPLATE = """\
Request context:
{metadata_block}

Standalone request:
{resolved_query}

Entities:
{known_entities}

Previous plan:
{previous_plan}

Sufficiency report (replan {replan_attempt} of {max_replans}):
{sufficiency_report}

What each agent returned so far:
{results_overview}"""
