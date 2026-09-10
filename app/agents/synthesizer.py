from __future__ import annotations

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

        logger.info("[SYNTH] completed reply_length=%s citations=%s", len(text), len(refs))
        return {"final_output": text, "cited_references": refs}


_synthesizer = SynthesizerAgent()


async def synthesizer_agent(state: AgentState) -> dict:
    return await _synthesizer.run(state)
