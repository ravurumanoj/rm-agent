"""Argument schemas for the tools exposed to the ReAct sub-agents (the LLM sees these descriptions)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PortfolioIdArgs(BaseModel):
    portfolio_id: str = Field(..., description="Portfolio identifier, e.g. 1111.")


class PortfolioHoldingsArgs(PortfolioIdArgs):
    top_n: int = Field(default=10, ge=1, le=50, description="Number of largest holdings to return.")


class PortfolioPerformanceArgs(PortfolioIdArgs):
    period: str = Field(
        default="YTD",
        description="Performance window such as 1M, 3M, 6M, YTD or 1Y.",
    )


class EmailSearchArgs(BaseModel):
    client_id: str = Field(..., description="Client identifier whose mailbox threads should be searched.")
    query: str = Field(default="", description="Optional keywords to narrow the email search.")
    limit: int = Field(default=5, ge=1, le=25, description="Maximum number of email threads to return.")


class MeetingArgs(BaseModel):
    client_id: str = Field(..., description="Client identifier whose meetings should be listed.")
    days_back: int = Field(default=90, ge=1, le=730, description="How many days back to look for meetings.")


class CallTranscriptArgs(BaseModel):
    client_id: str = Field(..., description="Client identifier whose call transcripts should be fetched.")
    limit: int = Field(default=3, ge=1, le=10, description="Maximum number of recent call transcripts to return.")
