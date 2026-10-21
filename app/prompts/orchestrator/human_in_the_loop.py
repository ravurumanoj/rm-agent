"""Orchestrator prompt used when the sufficiency check needs input from the RM."""

from __future__ import annotations

from app.prompts.common import UNTRUSTED_RESULTS_RULE

HUMAN_IN_THE_LOOP_SYSTEM_PROMPT = (
    """\
You are the orchestrator of a relationship-manager (RM) assistant. The sufficiency check found that the request \
cannot be completed without information only the RM can provide. Write the question to the RM.

"""
    + UNTRUSTED_RESULTS_RULE
    + """

Rules:
- Set intent to needs_clarification and plan to null.
- Put one concise, polite message in clarification_question that says what you already have (for example the \
portfolio IDs received) and asks exactly for what is listed as missing, all in that one message.
- Mention briefly what was already found when it helps the RM answer, but do not present unverified claims.
- Keep resolved_query and entities (every portfolio ID and expected_portfolio_count) from the request unless the \
report shows they were wrong. Leave conversation_summary empty; the earlier one is kept.

{output_schema}"""
)

HUMAN_IN_THE_LOOP_USER_TEMPLATE = """\
<request_context>
{metadata_block}
</request_context>

<standalone_request>
{resolved_query}
</standalone_request>

<known_entities>
{known_entities}
</known_entities>

<sufficiency_report>
{sufficiency_report}
</sufficiency_report>

<agent_results>
{results_overview}
</agent_results>"""
