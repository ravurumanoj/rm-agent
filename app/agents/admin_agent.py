"""Administrative agent: one direct call to an external agent (Unique integration, placeholder for now)."""

from __future__ import annotations

import asyncio
import time

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.agent_result import build_agent_result
from app.agents.base import BaseAgent
from app.components.messages import extract_text
from app.components.react.models import ToolCallRecord
from app.config import settings
from app.constants import ADMIN_EXTERNAL_TOOL_NAME, AGENT_ADMIN, AGENT_TERMINATED_SINGLE_CALL
from app.prompts.admin_agent import ADMIN_QUERY_SYSTEM_PROMPT, ADMIN_QUERY_USER_TEMPLATE
from app.prompts.react import EXTRA_INSTRUCTION_BLOCK_TEMPLATE, HANDOFF_BLOCK_TEMPLATE
from app.schemas.agent_io import AgentResult, AgentTask
from app.services.tracing import operation_span, record_exception
from app.tools.base import ExternalAgentClient
from app.tools.registry import get_tool_registry
from app.utils.logger import logger, preview


class AdminAgent(BaseAgent):
    """No reasoning loop: rewrites the request into an admin-only query, then makes one external call."""

    agent_id = AGENT_ADMIN

    def __init__(self, client: ExternalAgentClient | None = None) -> None:
        super().__init__()
        self._client = client

    @property
    def client(self) -> ExternalAgentClient:
        if self._client is None:
            self._client = get_tool_registry().admin
        return self._client

    async def build_query(self, task: AgentTask) -> str:
        handoff = HANDOFF_BLOCK_TEMPLATE.format(handoff_context=task.handoff_context) if task.handoff_context else ""
        extra = (
            EXTRA_INSTRUCTION_BLOCK_TEMPLATE.format(attempt=task.attempt, extra_instruction=task.extra_instruction)
            if task.extra_instruction
            else ""
        )
        user_prompt = ADMIN_QUERY_USER_TEMPLATE.format(
            objective=task.objective,
            user_query=task.user_query,
            handoff_block=handoff,
            extra_instruction_block=extra,
        )
        reply = await self.llm.ainvoke(
            [SystemMessage(content=ADMIN_QUERY_SYSTEM_PROMPT), HumanMessage(content=user_prompt)]
        )
        return extract_text(reply.content).strip()

    async def run(self, task: AgentTask) -> AgentResult:
        logger.info("[ADMIN] run_started attempt=%s objective=%s", task.attempt, preview(task.objective))
        started = time.perf_counter()
        query = task.objective
        output = None
        error: str | None = None
        with operation_span("agent.admin.external_call", kind="AGENT", attributes={"app.agent": self.agent_id}) as span:
            try:
                query = await self.build_query(task)
                logger.info("[ADMIN] query_rewritten query=%s", preview(query))
                # The SDK poll gives up at ADMIN_AGENT_TIMEOUT_SECONDS; this outer limit is only a backstop.
                output = await asyncio.wait_for(
                    self.client.ask(query, task.metadata), timeout=settings.ADMIN_AGENT_TIMEOUT_SECONDS + 10
                )
            except asyncio.TimeoutError:
                error = f"External agent timed out after {settings.ADMIN_AGENT_TIMEOUT_SECONDS:.0f}s."
                logger.warning("[ADMIN] external_call_timeout timeout_s=%s", settings.ADMIN_AGENT_TIMEOUT_SECONDS)
            except Exception as exc:  # degrade to an error result so the sufficiency check can decide
                error = f"{type(exc).__name__}: {exc}"
                logger.exception("[ADMIN] external_call_failed")
                record_exception(span, exc)

        record = ToolCallRecord(
            iteration=1,
            tool_name=ADMIN_EXTERNAL_TOOL_NAME,
            arguments={"query": query},
            output=output,
            error=error,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        result = build_agent_result(
            task,
            records=[record],
            summary=output.content if output else "",
            terminated_reason=AGENT_TERMINATED_SINGLE_CALL,
            iterations=1,
        )
        logger.info(
            "[ADMIN] run_completed status=%s duration_ms=%s error=%s output=%s",
            result.status, record.duration_ms, error, preview(result.summary),
        )
        return result
