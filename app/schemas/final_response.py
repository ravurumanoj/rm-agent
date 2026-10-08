"""Schema for the final agent's output."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.agent import CitationItem


class FinalAnswer(BaseModel):
    """Structured markdown answer plus the citations it actually uses."""

    markdown: str = Field(..., description="Answer text in structured markdown with [sourceN] markers.")
    citations: list[CitationItem] = Field(
        default_factory=list,
        description="Only the sources referenced by a [sourceN] marker in the markdown.",
    )
