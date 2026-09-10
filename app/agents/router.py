from __future__ import annotations

import json
import re
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import BaseAgent, extract_text
from app.constants import (
    EXEC_MODE_PARALLEL,
    EXEC_MODE_SEQUENTIAL,
    ROUTE_BOTH,
    ROUTE_CRM_ONLY,
    ROUTE_GENERAL,
    ROUTE_GREETING,
    ROUTE_OUT_OF_SCOPE,
    ROUTE_PORTFOLIO_ONLY,
    VALID_AGENTS,
    VALID_EXEC_MODES,
    VALID_ROUTES,
)
from app.prompts.router import (
    EXECUTION_PLANNER_SYSTEM_PROMPT,
    EXECUTION_PLANNER_USER_TEMPLATE,
    ROUTER_SYSTEM_PROMPT,
    ROUTER_USER_TEMPLATE,
)
from app.schemas.internal import AgentState, RouteDecision
from app.utils.logger import logger


class RouterAgent(BaseAgent):
    async def classify_and_route(self, state: AgentState) -> dict:
        user_msg = (state.get("user_message") or "").strip()
        history_block = state.get("history_block") or ""

        if not user_msg:
            return {"route": ROUTE_GREETING, "execution_mode": "", "producer": ""}

        decision = await self._decide(user_msg, history_block=history_block)
        logger.info("Router graph node decision route=%s mode=%s producer=%s", decision.route, decision.execution_mode, decision.producer)
        return {
            "route": decision.route,
            "execution_mode": decision.execution_mode or "",
            "producer": decision.producer or "",
        }

    async def _decide(self, user_msg: str, history_block: str = "") -> RouteDecision:
        route = await self._llm_classify(user_msg, history_block=history_block)
        if route != ROUTE_BOTH:
            return RouteDecision(route=route)

        execution_mode, producer = await self._plan_execution(user_msg, history_block)
        return RouteDecision(route=route, execution_mode=execution_mode, producer=producer)

    async def _llm_classify(self, user_msg: str, history_block: str = "") -> str:
        msgs = [
            SystemMessage(content=ROUTER_SYSTEM_PROMPT),
            HumanMessage(
                content=ROUTER_USER_TEMPLATE.format(
                    history_block=history_block,
                    user_message=user_msg,
                )
            ),
        ]
        try:
            result = await self.llm.ainvoke(msgs)
            raw = extract_text(result.content).strip().lower().rstrip(".")
            candidate = raw.split()[0] if raw.split() else ""
            logger.debug("[ROUTER] llm_classify raw_output=%r candidate=%r", raw[:200], candidate)

            if candidate in VALID_ROUTES:
                return candidate

            if "greet" in raw or "hello" in raw or "hi" == raw:
                return ROUTE_GREETING
            if "out" in raw and "scope" in raw:
                return ROUTE_OUT_OF_SCOPE
            if "crm" in raw or "interaction" in raw or "relationship" in raw:
                return ROUTE_CRM_ONLY
            if "portfolio" in raw or "invest" in raw or "holding" in raw:
                return ROUTE_PORTFOLIO_ONLY
            if "both" in raw:
                return ROUTE_BOTH

            logger.warning("Router produced unknown label '%s'; defaulting to general", raw)
            return ROUTE_GENERAL
        except Exception as exc:
            # Safe fallback when provider settings are incomplete.
            logger.warning("LLM router failed (%s); using heuristic fallback", exc)
            return self._heuristic_route(user_msg)

    async def _plan_execution(self, user_msg: str, history_block: str = "") -> tuple[str, Optional[str]]:
        msgs = [
            SystemMessage(content=EXECUTION_PLANNER_SYSTEM_PROMPT),
            HumanMessage(
                content=EXECUTION_PLANNER_USER_TEMPLATE.format(
                    history_block=history_block,
                    user_message=user_msg,
                )
            ),
        ]
        try:
            result = await self.llm.ainvoke(msgs)
            data = self._parse_json_object(extract_text(result.content))
            mode = str(data.get("execution_mode", "")).strip().lower()
            producer = data.get("producer")
            producer = str(producer).strip().lower() if producer else None
            logger.debug("[ROUTER] plan_execution raw_data=%s mode=%s producer=%s", data, mode, producer)

            if mode not in VALID_EXEC_MODES:
                return EXEC_MODE_PARALLEL, None
            if mode == EXEC_MODE_SEQUENTIAL:
                if producer not in VALID_AGENTS:
                    return EXEC_MODE_PARALLEL, None
                return EXEC_MODE_SEQUENTIAL, producer
            return EXEC_MODE_PARALLEL, None
        except Exception as exc:
            logger.warning("Execution planning failed (%s); defaulting to parallel", exc)
            return EXEC_MODE_PARALLEL, None

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

    @staticmethod
    def _heuristic_route(user_msg: str) -> str:
        text = user_msg.strip().lower()
        words = set(re.findall(r"\b[a-z]+\b", text))

        if "hello" in words or "hey" in words or "hi" in words or "good morning" in text or "good evening" in text:
            return ROUTE_GREETING
        if any(token in words for token in ["joke", "poem", "song", "recipe", "weather", "code"]):
            return ROUTE_OUT_OF_SCOPE

        has_portfolio = any(token in words for token in ["portfolio", "holding", "holdings", "aum", "performance", "allocation", "risk", "return"])
        has_crm = any(token in words for token in ["crm", "meeting", "meetings", "call", "followup", "follow-up", "interaction", "action", "client"])
        if has_portfolio and has_crm:
            logger.debug("[ROUTER] heuristic_route=both has_portfolio=%s has_crm=%s", has_portfolio, has_crm)
            return ROUTE_BOTH
        if has_portfolio:
            logger.debug("[ROUTER] heuristic_route=portfolio_only")
            return ROUTE_PORTFOLIO_ONLY
        if has_crm:
            logger.debug("[ROUTER] heuristic_route=crm_only")
            return ROUTE_CRM_ONLY
        logger.debug("[ROUTER] heuristic_route=general")
        return ROUTE_GENERAL


router_agent = RouterAgent().classify_and_route
