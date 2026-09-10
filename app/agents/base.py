from __future__ import annotations

from typing import Union

from langchain_core.messages import BaseMessage

from app.services.llm import get_llm

_shared_llm = None


def get_shared_llm():
    """Return a lazily-created process-wide LLM router instance."""
    global _shared_llm
    if _shared_llm is None:
        _shared_llm = get_llm()
    return _shared_llm


def extract_text(content: Union[str, list, None]) -> str:
    """Normalize chunk/message content to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                parts.append(part.get("text", "") or part.get("content", ""))
            else:
                parts.append(str(part))
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


class BaseAgent:
    """Base class for shared LLM and history helpers."""

    def __init__(self):
        self.llm = get_shared_llm()
