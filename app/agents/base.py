"""Shared LLM agent base: lazily created process-wide LLM."""

from __future__ import annotations

from app.services.llm import get_llm

_shared_llm = None


def get_shared_llm():
    """Return a lazily-created process-wide LLM router instance."""
    global _shared_llm
    if _shared_llm is None:
        _shared_llm = get_llm()
    return _shared_llm


class BaseAgent:
    """Gives agents a lazily resolved LLM; tests can set `_llm` directly."""

    def __init__(self) -> None:
        self._llm = None

    @property
    def llm(self):
        if self._llm is None:
            self._llm = get_shared_llm()
        return self._llm
