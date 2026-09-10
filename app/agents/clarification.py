from __future__ import annotations

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import BaseAgent, extract_text
from app.constants import CLARIFY_ASK, CLARIFY_PROCEED, VALID_CLARIFY_ACTIONS
from app.prompts.clarification import CLARIFICATION_SYSTEM_PROMPT, CLARIFICATION_USER_TEMPLATE
from app.schemas.internal import AgentState, ClarificationDecision
from app.utils.logger import logger


class ClarificationAgent(BaseAgent):
    async def assess(
        self,
        user_msg: str,
        *,
        client_id: str = "",
        active_portfolios: str = "(none specified)",
        selected_portfolio_ids: str = "(none selected)",
        unmatched_portfolio_ids: str = "(none)",
        client_roster: str = "(not provided)",
        history_block: str = "",
    ) -> ClarificationDecision:
        if not user_msg.strip():
            return ClarificationDecision(action=CLARIFY_PROCEED)

        msgs = [
            SystemMessage(content=CLARIFICATION_SYSTEM_PROMPT),
            HumanMessage(
                content=CLARIFICATION_USER_TEMPLATE.format(
                    client_id=client_id or "(none selected)",
                    active_portfolios=active_portfolios,
                    selected_portfolio_ids=selected_portfolio_ids,
                    unmatched_portfolio_ids=unmatched_portfolio_ids,
                    client_roster=client_roster,
                    history_block=history_block,
                    user_message=user_msg,
                )
            ),
        ]

        try:
            result = await self.llm.ainvoke(msgs)
            data = self._parse_json_object(extract_text(result.content))
            action = str(data.get("action", "")).strip().lower()

            if action not in VALID_CLARIFY_ACTIONS:
                return ClarificationDecision(action=CLARIFY_PROCEED)

            if action == CLARIFY_ASK:
                question = str(data.get("question", "")).strip()
                if question:
                    return ClarificationDecision(action=CLARIFY_ASK, question=question)
            return ClarificationDecision(action=CLARIFY_PROCEED)
        except Exception as exc:
            logger.warning("Clarification gate failed (%s); proceeding", exc)
            return ClarificationDecision(action=CLARIFY_PROCEED)

    @staticmethod
    def _parse_json_object(text: str) -> dict:
        text = text.strip()
        try:
            return json.loads(text)
        except Exception:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except Exception:
                    return {}
            return {}


_clarification_agent = ClarificationAgent()


async def clarification_agent(state: AgentState) -> dict:
    """LangGraph-compatible clarification gate node."""
    user_msg = (state.get("user_message") or "").strip()
    metadata = state.get("metadata") or {}
    decision = await _clarification_agent.assess(
        user_msg,
        client_id=str(metadata.get("client_id") or ""),
        active_portfolios=str(metadata.get("active_portfolios") or "(none specified)"),
        selected_portfolio_ids=", ".join(state.get("selected_portfolio_ids", [])) or "(none selected)",
        unmatched_portfolio_ids=", ".join(state.get("unmatched_portfolio_ids", [])) or "(none)",
        client_roster=str(metadata.get("client_roster") or "(not provided)"),
        history_block=state.get("history_block") or "",
    )
    return {
        "needs_clarification": decision.needs_clarification,
        "clarification_question": decision.question or "",
    }
