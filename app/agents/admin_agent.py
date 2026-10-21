"""Administrative agent: one direct call to an external agent (Unique integration, placeholder for now)."""

from __future__ import annotations

import asyncio
import re
import time

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.agent_result import build_agent_result
from app.agents.base import BaseAgent
from app.components.llm_call_context import llm_call
from app.components.messages import extract_text
from app.components.react.models import ToolCallRecord
from app.components.resilience import (
    CIRCUIT_OPEN,
    CircuitOpenError,
    ErrorKind,
    RetryStats,
    classify_error,
    get_circuit_breaker,
    is_timeout,
    retry_async,
)
from app.config import settings
from app.constants import ADMIN_EXTERNAL_TOOL_NAME, AGENT_ADMIN, AGENT_TERMINATED_SINGLE_CALL
from app.prompts.admin_agent import ADMIN_QUERY_SYSTEM_PROMPT, ADMIN_QUERY_USER_TEMPLATE
from app.prompts.react import CONVERSATION_BLOCK_TEMPLATE, EXTRA_INSTRUCTION_BLOCK_TEMPLATE, HANDOFF_BLOCK_TEMPLATE
from app.schemas.agent_io import AgentResult, AgentTask
from app.observability import start_agent_span, start_tool_span
from app.services.observability import record_tool_call
from app.tools.base import ExternalAgentClient
from app.tools.registry import get_tool_registry
from app.utils.logger import logger, preview


ADMIN_BREAKER_KEY = "external:admin_clm"
_QUERY_LABEL = re.compile(r"^\s*(?:rewritten\s+)?query\s*:\s*", re.IGNORECASE)


def _clean_query(text: str) -> str:
    """Strip the label and wrapping quotes models sometimes add despite the prompt."""
    query = _QUERY_LABEL.sub("", text.strip())
    return query.strip().strip('"\'`').strip()


class AdminAgent(BaseAgent):
    """No reasoning loop: rewrites the request into one admin-only query, then makes one external call with it.

    Only the rewritten query reaches the external assistant; the RM message and history stay on our side.
    """

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
        """Admin-only query for the external assistant; falls back to the orchestrator's objective."""
        try:
            query = _clean_query(await self._rewrite_query(task))
        except Exception:  # a failed rewrite must not cost the external call
            logger.warning("[ADMIN] query_rewrite_failed; using the objective", exc_info=True)
            return task.objective
        if not query:
            logger.warning("[ADMIN] query_rewrite_empty; using the objective")
        return query or task.objective

    async def _rewrite_query(self, task: AgentTask) -> str:
        handoff = HANDOFF_BLOCK_TEMPLATE.format(handoff_context=task.handoff_context) if task.handoff_context else ""
        extra = (
            EXTRA_INSTRUCTION_BLOCK_TEMPLATE.format(attempt=task.attempt, extra_instruction=task.extra_instruction)
            if task.extra_instruction
            else ""
        )
        conversation = (
            CONVERSATION_BLOCK_TEMPLATE.format(conversation_summary=task.conversation_summary)
            if task.conversation_summary
            else ""
        )
        user_prompt = ADMIN_QUERY_USER_TEMPLATE.format(
            objective=task.objective,
            user_query=task.user_query,
            rm_message=task.rm_message or task.user_query,
            conversation_block=conversation,
            handoff_block=handoff,
            extra_instruction_block=extra,
        )
        with llm_call("query_rewrite"):
            reply = await self.llm.ainvoke(
                [SystemMessage(content=ADMIN_QUERY_SYSTEM_PROMPT), HumanMessage(content=user_prompt)]
            )
        return extract_text(reply.content)

    async def run(self, task: AgentTask) -> AgentResult:
        started = time.perf_counter()
        failure: Exception | None = None
        query = task.objective
        output = None
        error: str | None = None
        error_kind: str | None = None
        stats = RetryStats(attempts=0)
        breaker = get_circuit_breaker(
            ADMIN_BREAKER_KEY,
            failure_threshold=settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
            cooldown_seconds=settings.CIRCUIT_BREAKER_COOLDOWN_SECONDS,
        )
        with start_agent_span(self.agent_id, input_value=task.objective, attributes={"app.agent.attempt": task.attempt}):
            query = await self.build_query(task)  # never raises: falls back to the objective
            with start_tool_span(ADMIN_EXTERNAL_TOOL_NAME, {"query": query}) as tool_span:
                try:
                    if not breaker.allow():
                        raise breaker.unavailable_error()
                    # The SDK poll gives up at ADMIN_AGENT_TIMEOUT_SECONDS; the outer limit is only a backstop. A
                    # timeout is not retried (one wait can already take minutes); connection drops, 5xx and 429 are.
                    output = await retry_async(
                        lambda: asyncio.wait_for(
                            self.client.ask(query, task.metadata), timeout=settings.ADMIN_AGENT_TIMEOUT_SECONDS + 10
                        ),
                        max_retries=settings.ADMIN_AGENT_MAX_RETRIES,
                        base_delay=settings.TOOL_RETRY_BASE_DELAY,
                        max_delay=settings.TOOL_RETRY_MAX_DELAY,
                        jitter_ratio=settings.RETRY_JITTER_RATIO,
                        retry_on_timeout=False,
                        label="admin CLM call",
                        stats=stats,
                    )
                    breaker.record_success()
                except Exception as exc:  # degrade to an error result so the sufficiency check can decide
                    kind = classify_error(exc)
                    error_kind = CIRCUIT_OPEN if isinstance(exc, CircuitOpenError) else kind.value
                    if is_timeout(exc):
                        error = f"External agent timed out after {settings.ADMIN_AGENT_TIMEOUT_SECONDS:.0f}s."
                    else:
                        error = f"{type(exc).__name__}: {exc}"
                    if kind is ErrorKind.RETRYABLE and not isinstance(exc, CircuitOpenError):
                        breaker.record_failure()
                    failure = exc

                record = ToolCallRecord(
                    iteration=1,
                    tool_name=ADMIN_EXTERNAL_TOOL_NAME,
                    arguments={"query": query},
                    output=output,
                    error=error,
                    error_kind=error_kind,
                    attempts=max(1, stats.attempts),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
                record_tool_call(tool_span, record)

        result = build_agent_result(
            task,
            records=[record],
            summary=output.content if output else "",
            terminated_reason=AGENT_TERMINATED_SINGLE_CALL,
            iterations=1,
        )
        # Unexpected failures keep their traceback; expected ones (timeout, circuit open, retryable) do not.
        expected = failure is None or isinstance(failure, CircuitOpenError) or error_kind == ErrorKind.RETRYABLE.value
        (logger.info if failure is None else logger.warning)(
            "[ADMIN] run_completed status=%s attempt=%s duration_ms=%s clm_attempts=%s error_kind=%s sources=%s "
            "query=%s error=%s output=%s",
            result.status, task.attempt, record.duration_ms, record.attempts, error_kind or "-", len(result.sources),
            preview(query), error or "-", preview(result.summary),
            exc_info=None if expected else failure,
        )
        return result
