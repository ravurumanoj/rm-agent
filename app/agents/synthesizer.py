from __future__ import annotations

import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import BaseAgent, extract_text
from app.constants import ROUTE_GREETING, ROUTE_OUT_OF_SCOPE, SAFE_DECLINE_MESSAGE
from app.prompts.direct_reply import DIRECT_REPLY_SYSTEM_PROMPT, DIRECT_REPLY_USER_TEMPLATE
from app.prompts.synthesizer import (
    SYNTHESIZER_PARTIAL_NOTICE,
    SYNTHESIZER_SYSTEM_PROMPT,
    SYNTHESIZER_USER_TEMPLATE,
)
from app.schemas.internal import AgentState
from app.utils.logger import logger


class SynthesizerAgent(BaseAgent):
    _LEAKED_INSTRUCTION_PATTERNS = (
        r"state that section is unavailable[^.]*\.?",
        r"briefly say that the section is unavailable[^.]*\.?",
        r"do not repeat these instructions in the answer[^.]*\.?",
        r"do not repeat this note verbatim[^.]*\.?",
        r"do not fabricate the missing information[^.]*\.?",
        r"in one line and continue[^.]*\.?",
    )

    @staticmethod
    def _sanitize_uncited_markers(text: str, refs: list[dict]) -> str:
        if refs:
            allowed_markers = {
                str(ref.get("marker") or "").strip().lower()
                for ref in refs
                if isinstance(ref, dict) and str(ref.get("marker") or "").strip()
            }

            def _replace_marker(match: re.Match[str]) -> str:
                marker = match.group(0)
                return marker if marker.lower() in allowed_markers else ""

            cleaned = re.sub(r"\[[^\]]+\]", _replace_marker, text)
            return re.sub(r"\s{2,}", " ", cleaned).strip()
        # Remove citation-like markers when no references are available.
        cleaned = re.sub(r"\[[^\]]+\]", "", text)
        return re.sub(r"\s{2,}", " ", cleaned).strip()

    @classmethod
    def _sanitize_instruction_leakage(cls, text: str) -> str:
        cleaned = text
        for pattern in cls._LEAKED_INSTRUCTION_PATTERNS:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        cleaned = re.sub(r"(?:\n\s*){3,}", "\n\n", cleaned)
        return cleaned.strip(" \n:-")

    @staticmethod
    def _has_effective_result(value: object) -> bool:
        if isinstance(value, dict):
            if value.get("error"):
                return False
            chunks = value.get("chunks")
            if isinstance(chunks, list) and bool(chunks):
                return True
            nested = value.get("results_by_portfolio_id")
            if isinstance(nested, dict):
                return any(SynthesizerAgent._has_effective_result(item) for item in nested.values())
            if value.get("data") not in (None, {}, []):
                return True
            return False
        return value is not None

    def _has_effective_data(self, state: AgentState) -> bool:
        for output_key in ("portfolio_output", "crm_output"):
            tool_results = ((state.get(output_key) or {}).get("tool_results") or {})
            if not isinstance(tool_results, dict):
                continue
            for result in tool_results.values():
                if self._has_effective_result(result):
                    return True
        return False

    def _first_portfolio_snapshot(self, state: AgentState) -> dict | None:
        def _find_snapshot(value: object) -> dict | None:
            if not isinstance(value, dict):
                return None

            direct_data = value.get("data")
            if isinstance(direct_data, dict):
                summary = direct_data.get("portfolio_summary")
                if isinstance(summary, dict):
                    return {
                        "portfolio_id": str(value.get("portfolio_id") or "").strip(),
                        "as_of_date": str(summary.get("as_of_date") or "not stated"),
                        "currency": str(summary.get("currency") or "not stated"),
                        "current_value": summary.get("current_value", "not stated"),
                        "invested_value": summary.get("invested_value", "not stated"),
                        "unrealized_pnl": summary.get("unrealized_pnl", "not stated"),
                        "unrealized_pnl_pct": summary.get("unrealized_pnl_pct", "not stated"),
                    }

            nested = value.get("results_by_portfolio_id")
            if isinstance(nested, dict):
                for nested_value in nested.values():
                    found = _find_snapshot(nested_value)
                    if found is not None:
                        return found
            return None

        tool_results = ((state.get("portfolio_output") or {}).get("tool_results") or {})
        if not isinstance(tool_results, dict):
            return None
        for result in tool_results.values():
            found = _find_snapshot(result)
            if found is not None:
                return found
        return None

    @staticmethod
    def _render_portfolio_snapshot(snapshot: dict) -> str:
        portfolio_id = snapshot.get("portfolio_id") or "not stated"
        as_of = snapshot.get("as_of_date") or "not stated"
        currency = snapshot.get("currency") or "not stated"
        current_value = snapshot.get("current_value", "not stated")
        invested_value = snapshot.get("invested_value", "not stated")
        unrealized_pnl = snapshot.get("unrealized_pnl", "not stated")
        unrealized_pnl_pct = snapshot.get("unrealized_pnl_pct", "not stated")

        return (
            f"Portfolio data: As of {as_of} | Portfolio {portfolio_id} | {currency} | "
            f"Current Value: {current_value} | Invested Value: {invested_value} | "
            f"Unrealized PnL: {unrealized_pnl} ({unrealized_pnl_pct}%)."
        )

    @staticmethod
    def _deterministic_unavailable_message(state: AgentState) -> str:
        missing = [
            str(section).strip().lower()
            for section in (state.get("retrieval_missing_sections") or [])
            if str(section).strip()
        ]
        route = str(state.get("route") or "")

        include_portfolio = ("portfolio data" in missing) or route in {"portfolio_only", "both"}
        include_crm = ("crm data" in missing) or route in {"crm_only", "both"}

        parts: list[str] = []
        if include_portfolio:
            parts.append("Portfolio data is unavailable.")
        if include_crm:
            parts.append("CRM data is unavailable.")
        return "\n\n".join(parts) if parts else "Required data is unavailable."

    def _build_context(self, state: AgentState) -> tuple[str, list[dict]]:
        parts: list[str] = []
        refs = state.get("citation_references", []) or []

        portfolio_output = state.get("portfolio_output") or {}
        for tool_name, result in (portfolio_output.get("tool_results") or {}).items():
            parts.append(f"--- SOURCE: Portfolio ({tool_name}) ---\n{result}")

        crm_output = state.get("crm_output") or {}
        for tool_name, result in (crm_output.get("tool_results") or {}).items():
            parts.append(f"--- SOURCE: CRM ({tool_name}) ---\n{result}")

        if not parts:
            raw_outputs = state.get("tool_outputs") or []
            for item in raw_outputs:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("domain") or item.get("tool") or "source")
                data = item.get("result") or item.get("content") or item
                parts.append(f"--- SOURCE: {label} ---\n{data}")

        missing_sections = [
            str(section).strip()
            for section in (state.get("retrieval_missing_sections") or [])
            if str(section).strip()
        ]
        if missing_sections:
            parts.append(
                SYNTHESIZER_PARTIAL_NOTICE.format(
                    missing_data=", ".join(missing_sections),
                )
            )

        return "\n\n".join(parts) if parts else "(no data retrieved)", refs

    async def run(self, state: AgentState) -> dict:
        user_message = (state.get("user_message") or "").strip()
        route = state.get("route", "general")
        logger.info("[SYNTH] run route=%s", route)

        if route == ROUTE_OUT_OF_SCOPE:
            return {"final_output": SAFE_DECLINE_MESSAGE, "cited_references": []}

        if route == ROUTE_GREETING:
            try:
                msgs = [
                    SystemMessage(content=DIRECT_REPLY_SYSTEM_PROMPT),
                    HumanMessage(content=DIRECT_REPLY_USER_TEMPLATE.format(history_block="", user_message=user_message)),
                ]
                result = await self.llm.ainvoke(msgs)
                reply = extract_text(result.content).strip()
                return {"final_output": reply or "Hello! How can I help with your RM workflow today?", "cited_references": []}
            except Exception:
                logger.warning("[SYNTH] greeting_llm_failed_using_fallback")
                return {"final_output": "Hello! How can I help with your RM workflow today?", "cited_references": []}

        context, refs = self._build_context(state)
        client_id = str((state.get("metadata") or {}).get("client_id") or "unknown")

        snapshot = self._first_portfolio_snapshot(state)
        route = str(state.get("route") or "")
        if snapshot is not None and not refs and route in {"portfolio_only", "both"}:
            text = self._render_portfolio_snapshot(snapshot)
            text = self._sanitize_uncited_markers(text, refs)
            logger.info("[SYNTH] completed_deterministic_portfolio_summary reply_length=%s", len(text))
            return {"final_output": text, "cited_references": []}

        if not refs and not self._has_effective_data(state):
            text = self._deterministic_unavailable_message(state)
            logger.info("[SYNTH] completed_deterministic_unavailable reply_length=%s", len(text))
            return {"final_output": text, "cited_references": []}

        try:
            msgs = [
                SystemMessage(content=SYNTHESIZER_SYSTEM_PROMPT),
                HumanMessage(
                    content=SYNTHESIZER_USER_TEMPLATE.format(
                        query=user_message,
                        client_id=client_id,
                        context=context,
                    )
                ),
            ]
            result = await self.llm.ainvoke(msgs)
            text = extract_text(result.content).strip()
        except Exception:
            logger.warning("[SYNTH] llm_synthesis_failed_using_fallback")
            text = ""

        if not text:
            if refs:
                marker_str = " ".join(ref.get("marker", "") for ref in refs if ref.get("marker"))
                text = (
                    "Based on the retrieved context, here is the best available response "
                    f"for your request: '{user_message}'. {marker_str}".strip()
                )
            else:
                text = f"I understood your request: '{user_message}'."

        text = self._sanitize_instruction_leakage(text)
        text = self._sanitize_uncited_markers(text, refs)

        logger.info("[SYNTH] completed reply_length=%s citations=%s", len(text), len(refs))
        return {"final_output": text, "cited_references": refs}


_synthesizer = SynthesizerAgent()


async def synthesizer_agent(state: AgentState) -> dict:
    return await _synthesizer.run(state)
