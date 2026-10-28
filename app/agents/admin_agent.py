"""Administrative agent: one direct call to an external agent (the CLM assistant in Unique)."""

from __future__ import annotations

import asyncio
import time

from app.agents.agent_result import build_agent_result
from app.agents.base import BaseAgent
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
from app.constants import ADMIN_EXTERNAL_TOOL_NAME, AGENT_ADMIN, AGENT_TERMINATED_SINGLE_CALL, AGENT_TO_NODE
from app.schemas.agent_io import AgentResult, AgentTask
from app.observability import Attr, start_agent_span, start_tool_span
from app.services.observability import record_agent_result, record_tool_call
from app.tools.base import ExternalAgentClient
from app.tools.registry import get_tool_registry
from app.utils.logger import logger, preview


ADMIN_BREAKER_KEY = "external:admin_clm"


class AdminAgent(BaseAgent):
    """No reasoning loop: sends the orchestrator's objective (already standalone and free of client data) to the
    external assistant in one call. The RM message and history stay on our side.
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

    async def run(self, task: AgentTask) -> AgentResult:
        started = time.perf_counter()
        failure: Exception | None = None
        query = task.objective.strip()
        output = None
        error: str | None = None
        error_kind: str | None = None
        stats = RetryStats(attempts=0)
        breaker = get_circuit_breaker(
            ADMIN_BREAKER_KEY,
            failure_threshold=settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
            cooldown_seconds=settings.CIRCUIT_BREAKER_COOLDOWN_SECONDS,
        )
        with start_agent_span(
            self.agent_id,
            input_value=task.objective,
            graph_node=AGENT_TO_NODE.get(self.agent_id, self.agent_id),
            attributes={"app.agent.attempt": task.attempt, Attr.GRAPH_STAGE: task.stage},
        ) as agent_span:
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
            record_agent_result(agent_span, result)
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
