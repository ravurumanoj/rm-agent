"""Names for LLM calls, carried through async code so traces show which node, agent and step made each call.

Usage:
    with llm_call("routing"):          # inside the orchestrator node -> "orchestrator.routing"
        await llm.ainvoke(messages)
Graph nodes open the first level automatically; callers add more specific levels. The LLM layer reads the
joined name when it opens its span.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_call_name: ContextVar[str] = ContextVar("llm_call_name", default="")


def current_llm_call_name() -> str:
    """Full name of the current call, for example "portfolio_agent.react.step_2"; empty outside any label."""
    return _call_name.get()


@contextmanager
def llm_call(name: str) -> Iterator[None]:
    """Label LLM calls made inside the block; nested labels are joined with dots."""
    parent = _call_name.get()
    token = _call_name.set(f"{parent}.{name}" if parent and name else (name or parent))
    try:
        yield
    finally:
        _call_name.reset(token)
