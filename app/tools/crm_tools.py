"""Placeholder CRM tools. Replace with Outlook MCP (email, meetings) and Fano MCP (transcripts) calls."""

from __future__ import annotations

from langchain_core.tools import BaseTool, StructuredTool

from app.schemas.tool_args import CallTranscriptArgs, EmailSearchArgs, MeetingArgs
from app.tools.base import dummy_tool_output


async def _search_client_emails(client_id: str, query: str = "", limit: int = 5) -> dict:
    return dummy_tool_output(
        f"crm-emails:{client_id}:{query}:{limit}", source_prefix="outlook-mail", title=f"Emails with client {client_id}"
    )


async def _get_client_meetings(client_id: str, days_back: int = 90) -> dict:
    return dummy_tool_output(
        f"crm-meetings:{client_id}:{days_back}",
        source_prefix="outlook-meeting",
        title=f"Meetings with client {client_id}",
    )


async def _get_call_transcripts(client_id: str, limit: int = 3) -> dict:
    return dummy_tool_output(
        f"crm-transcripts:{client_id}:{limit}",
        source_prefix="fano-call",
        title=f"Call transcripts for client {client_id}",
    )


class PlaceholderCrmToolProvider:
    """Stand-in for the Outlook and Fano MCP server tools."""

    def get_tools(self) -> list[BaseTool]:
        return [
            StructuredTool.from_function(
                coroutine=_search_client_emails,
                name="search_client_emails",
                description="Search email threads with a client (Outlook). Use for written concerns and commitments.",
                args_schema=EmailSearchArgs,
            ),
            StructuredTool.from_function(
                coroutine=_get_client_meetings,
                name="get_client_meetings",
                description="List recent meetings with a client (Outlook calendar) including notes and attendees.",
                args_schema=MeetingArgs,
            ),
            StructuredTool.from_function(
                coroutine=_get_call_transcripts,
                name="get_call_transcripts",
                description="Fetch recent call transcripts with a client (Fano). Use for spoken concerns and sentiment.",
                args_schema=CallTranscriptArgs,
            ),
        ]
