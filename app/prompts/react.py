"""Prompt fragments shared by the ReAct sub-agents (portfolio, CRM)."""

from __future__ import annotations

REACT_OPERATING_RULES = """\
Work in a reason-then-act loop:
- Before calling tools, think briefly about what is still missing for your objective.
- Call every independent tool in the same step so they run in parallel. Never repeat a call with identical arguments.
- Read each observation. If something needed is missing or a tool failed, try a different tool or different arguments.
- Stop calling tools as soon as the objective is covered. You have at most {max_iterations} reasoning steps.
- Your final message is a concise findings summary for the orchestrator: key facts with figures, dates and names \
exactly as returned, then a line starting with "Not found:" listing anything you could not retrieve. No speculation."""

REACT_FORCE_FINAL_MESSAGE = (
    "You have reached the step limit. Write your final findings summary now from the observations so far. "
    'List anything you could not retrieve on a line starting with "Not found:".'
)

AGENT_TASK_USER_TEMPLATE = """\
Objective:
{objective}

RM question (context only):
{user_query}

Request context:
{context_block}

Scope:
{entities_block}
{handoff_block}{extra_instruction_block}"""

HANDOFF_BLOCK_TEMPLATE = """
Findings from other agents (use them to target your retrieval; do not repeat them):
{handoff_context}
"""

EXTRA_INSTRUCTION_BLOCK_TEMPLATE = """
Re-fetch guidance (attempt {attempt}; earlier attempts were insufficient):
{extra_instruction}
"""
