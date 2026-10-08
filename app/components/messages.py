"""LLM message helpers."""

from __future__ import annotations

from typing import Any


def extract_text(content: Any) -> str:
    """Normalize message content (string, list of parts or None) to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            (part.get("text", "") or part.get("content", "")) if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)
