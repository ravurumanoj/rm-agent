"""Final agent: the LLM writes the structured markdown answer; code only keeps citations honest."""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.base import BaseAgent
from app.agents.formatting import format_sufficiency_report
from app.components.llm_call_context import llm_call
from app.components.messages import extract_text
from app.graph.state import GraphState
from app.constants import FALLBACK_FINAL_ANSWER
from app.observability import record_current_failure
from app.prompts.final_agent import (
    FINAL_AGENT_SYSTEM_PROMPT,
    FINAL_AGENT_USER_TEMPLATE,
    FINAL_CITE_INSTRUCTION,
    FINAL_REGENERATE_INSTRUCTION,
)
from app.schemas.agent import CitationItem
from app.schemas.final_response import FinalAnswer
from app.services.citations import ReferenceManager
from app.utils.logger import logger, preview

_MARKER_RE = re.compile(r"\[source\d+\]", re.IGNORECASE)


def strip_unknown_markers(text: str, references: list[dict[str, Any]]) -> str:
    """Remove [sourceN] markers the model invented; keep only markers backed by a reference."""
    allowed = {str(ref["marker"]).lower() for ref in references}
    cleaned = _MARKER_RE.sub(lambda m: m.group(0) if m.group(0).lower() in allowed else "", text)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def select_cited_references(text: str, references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    used = {marker.lower() for marker in _MARKER_RE.findall(text)}
    return [ref for ref in references if str(ref["marker"]).lower() in used]


class FinalAgent(BaseAgent):
    async def write(self, state: GraphState) -> FinalAnswer:
        references = state.get("citation_references") or []
        user_prompt = FINAL_AGENT_USER_TEMPLATE.format_map(
            {
                "resolved_query": state.get("resolved_query", ""),
                "rm_message": state.get("user_message", ""),
                "evidence": "\n\n".join((state.get("evidence") or {}).values()) or "(no agent output)",
                "source_markers": ReferenceManager.from_citations(references).build_reference_context() or "(none)",
                "sufficiency_report": format_sufficiency_report(state.get("sufficiency")),
            }
        )
        logger.debug("[FINAL] user_prompt=%s", preview(user_prompt))
        messages = [SystemMessage(content=FINAL_AGENT_SYSTEM_PROMPT), HumanMessage(content=user_prompt)]
        text, regenerated, failure = "", False, None
        try:
            # LLM-level retries with back-off already happen in the router; this handles unusable output.
            with llm_call("answer"):
                text = await self._generate(messages, references)
            if not text:
                regenerated = True  # empty or blocked: once more with a stricter instruction
                with llm_call("regenerate"):
                    text = await self._generate(
                        [*messages, HumanMessage(content=FINAL_REGENERATE_INSTRUCTION)], references
                    )
        except Exception as exc:
            failure = exc
        if text and references and not select_cited_references(text, references):
            text = await self._add_citations(messages, text, references)
        cited = select_cited_references(text, references) if text else []
        degraded = not text
        log = logger.error if degraded else (logger.warning if regenerated else logger.info)
        log(
            "[FINAL] completed degraded=%s regenerated=%s verdict=%s evidence_agents=%s references=%s cited=%s "
            "reply_length=%s reply=%s",
            degraded, regenerated, getattr(state.get("sufficiency"), "verdict", "-"),
            list((state.get("evidence") or {}).keys()), len(references), len(cited), len(text),
            preview(text or FALLBACK_FINAL_ANSWER),
            exc_info=failure,
        )
        if degraded:
            record_current_failure(
                failure or "the model returned no usable text, also after the stricter regenerate",
                where="final answer could not be written; sent the fallback answer",
                kind="fallback",
            )
            return FinalAnswer(markdown=FALLBACK_FINAL_ANSWER, citations=[], degraded=True)
        return FinalAnswer(markdown=text, citations=[CitationItem(**ref) for ref in cited])

    async def _add_citations(self, messages: list, text: str, references: list[dict[str, Any]]) -> str:
        """Sources exist but the answer cites none, so the UI would show no references: ask once to add them."""
        try:
            with llm_call("add_citations"):
                cited_text = await self._generate(
                    [*messages, AIMessage(content=text), HumanMessage(content=FINAL_CITE_INSTRUCTION)], references
                )
        except Exception:
            logger.warning("[FINAL] add_citations_failed; keeping the uncited answer", exc_info=True)
            return text
        if cited_text and select_cited_references(cited_text, references):
            logger.info("[FINAL] citations_added references=%s", len(select_cited_references(cited_text, references)))
            return cited_text
        logger.warning("[FINAL] answer_without_citations references=%s; keeping the first answer", len(references))
        return text

    async def _generate(self, messages: list, references: list[dict[str, Any]]) -> str:
        response = await self.llm.ainvoke(messages)
        return strip_unknown_markers(extract_text(response.content).strip(), references)
