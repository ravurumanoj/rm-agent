"""Final agent: the LLM writes the structured markdown answer; code only keeps citations honest."""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import BaseAgent
from app.agents.formatting import format_sufficiency_report
from app.components.messages import extract_text
from app.graph.state import GraphState
from app.prompts.final_agent import FINAL_AGENT_SYSTEM_PROMPT, FINAL_AGENT_USER_TEMPLATE
from app.schemas.agent import CitationItem
from app.schemas.final_response import FinalAnswer
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
                "evidence": "\n\n".join((state.get("evidence") or {}).values()) or "(no agent output)",
                "source_markers": "\n".join(f"{ref['marker']} {ref['title'] or ref['content_id']}" for ref in references)
                or "(none)",
                "sufficiency_report": format_sufficiency_report(state.get("sufficiency")),
            }
        )
        logger.info(
            "[FINAL] writing query=%s evidence_agents=%s references=%s",
            preview(state.get("resolved_query", "")), list((state.get("evidence") or {}).keys()), len(references),
        )
        logger.debug("[FINAL] user_prompt=%s", preview(user_prompt))
        response = await self.llm.ainvoke(
            [SystemMessage(content=FINAL_AGENT_SYSTEM_PROMPT), HumanMessage(content=user_prompt)]
        )
        text = strip_unknown_markers(extract_text(response.content).strip(), references)
        cited = select_cited_references(text, references)
        logger.info(
            "[FINAL] completed reply_length=%s citations=%s reply=%s", len(text), len(cited), preview(text)
        )
        return FinalAnswer(markdown=text, citations=[CitationItem(**ref) for ref in cited])
