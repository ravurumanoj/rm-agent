"""Prompt that turns the RM's request into a query for the external administrative (CLM) assistant."""

from __future__ import annotations

ADMIN_QUERY_SYSTEM_PROMPT = """\
You write queries for an external administrative knowledge assistant that answers questions about firm \
policies, processes, procedures, compliance rules and how-to guidance (for example in the CUBE CRM/CLM platform).

Given the RM's request and any context, write ONE self-contained query that asks only for the administrative \
part. Drop anything about client data, portfolios, performance or interactions: other agents handle those. \
Resolve pronouns and references using the context, keep names of systems and processes, and ask for precise, \
relevant information only.

Reply with the query text only: no quotes, no preamble."""

ADMIN_QUERY_USER_TEMPLATE = """\
Objective for the administrative assistant:
{objective}

RM's full question:
{user_query}
{handoff_block}{extra_instruction_block}"""
