"""Tool-calling sub-agent: one class configured per domain (portfolio, client interaction) with its own prompt and tools."""

from __future__ import annotations

import time
from dataclasses import replace
from functools import partial

from app.agents.agent_result import build_agent_result
from app.agents.base import BaseAgent
from app.agents.formatting import format_entities, format_metadata_block
from app.components.react import ReActConfig, ReActRunner
from app.config import settings
from app.constants import AGENT_STATUS_NO_DATA, AGENT_STATUS_OK
from app.prompts.react import (
    AGENT_TASK_USER_TEMPLATE,
    CONVERSATION_BLOCK_TEMPLATE,
    EXTRA_INSTRUCTION_BLOCK_TEMPLATE,
    HANDOFF_BLOCK_TEMPLATE,
    REACT_FORCE_FINAL_MESSAGE,
)
from app.schemas.agent_io import AgentResult, AgentTask
from app.observability import record_error, start_agent_span, start_tool_span
from app.services.map_reduce import agent_goal, reduce_large_observations
from app.services.observability import record_tool_call
from app.tools.base import ToolProvider
from app.utils.logger import logger, preview


class ReActAgent(BaseAgent):
    def __init__(self, agent_id: str, system_prompt: str, tool_provider: ToolProvider) -> None:
        super().__init__()
        self.agent_id = agent_id
        self._system_prompt = system_prompt
        self._tool_provider = tool_provider
        self._config = ReActConfig(
            max_iterations=settings.MAX_AGENT_ITERATIONS,
            tool_timeout_seconds=settings.AGENT_TOOL_TIMEOUT_SECONDS,
            max_parallel_tool_calls=settings.AGENT_MAX_PARALLEL_TOOL_CALLS,
            force_final_message=REACT_FORCE_FINAL_MESSAGE,
            max_retries=settings.TOOL_MAX_RETRIES,
            retry_base_delay=settings.TOOL_RETRY_BASE_DELAY,
            retry_max_delay=settings.TOOL_RETRY_MAX_DELAY,
            retry_jitter_ratio=settings.RETRY_JITTER_RATIO,
            retry_on_timeout=settings.TOOL_RETRY_ON_TIMEOUT,
            # Each agent's tools come from one server (portfolio: AAA MCP; client interaction: CRM MCP).
            breaker_key=f"tools:{agent_id}",
            breaker_failure_threshold=settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
            breaker_cooldown_seconds=settings.CIRCUIT_BREAKER_COOLDOWN_SECONDS,
            tool_span=start_tool_span,  # one TOOL span per MCP tool call
            on_tool_result=record_tool_call,
        )

    def build_user_prompt(self, task: AgentTask) -> str:
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
        return AGENT_TASK_USER_TEMPLATE.format(
            objective=task.objective,
            user_query=task.user_query,
            rm_message=task.rm_message or task.user_query,
            conversation_block=conversation,
            context_block=format_metadata_block(task.metadata),
            entities_block=format_entities(task.entities),
            handoff_block=handoff,
            extra_instruction_block=extra,
        )

    def _run_config(self, task: AgentTask) -> ReActConfig:
        """Per-run config: the map-reduce hook needs this task's objective as its goal."""
        if not settings.MAP_REDUCE_ENABLED:
            return self._config
        return replace(
            self._config,
            reduce_observations=partial(
                reduce_large_observations,
                llm=self.llm,
                goal=agent_goal(task.objective, task.user_query),
                context=format_entities(task.entities),
            ),
        )

    async def run(self, task: AgentTask) -> AgentResult:
        label = self.agent_id.upper()
        started = time.perf_counter()
        user_prompt = self.build_user_prompt(task)
        logger.debug(
            "[%s] run_input attempt=%s portfolio_ids=%s objective=%s user_prompt=%s",
            label, task.attempt, task.entities.portfolio_ids, preview(task.objective), preview(user_prompt),
        )
        with start_agent_span(
            self.agent_id, input_value=task.objective, attributes={"app.agent.attempt": task.attempt}
        ) as span:
            try:
                runner = ReActRunner(
                    self.llm,
                    self._tool_provider.get_tools(),
                    system_prompt=self._system_prompt.format(max_iterations=self._config.max_iterations),
                    config=self._run_config(task),
                )
                outcome = await runner.run(user_prompt)
            except Exception as exc:  # an agent crash degrades to an error result instead of aborting the graph
                logger.exception(
                    "[%s] run_crashed attempt=%s duration_ms=%s objective=%s",
                    label, task.attempt, int((time.perf_counter() - started) * 1000), preview(task.objective),
                )
                record_error(span, exc)
                return build_agent_result(task, records=[], summary="", terminated_reason="crashed", error=str(exc))

        result = build_agent_result(
            task,
            records=outcome.records,
            summary=outcome.final_text,
            terminated_reason=outcome.terminated_reason,
            steps=outcome.steps,
            iterations=outcome.iterations,
            error=outcome.error,
        )
        failed = [record.tool_name for record in result.tool_calls if not record.succeeded]
        healthy = result.status in (AGENT_STATUS_OK, AGENT_STATUS_NO_DATA) and not result.error
        (logger.info if healthy else logger.warning)(
            "[%s] run_completed status=%s attempt=%s duration_ms=%s iterations=%s terminated=%s tools=%s "
            "failed_tools=%s map_reduced=%s sources=%s error=%s summary=%s",
            label, result.status, task.attempt, int((time.perf_counter() - started) * 1000), result.iterations,
            result.terminated_reason, [record.tool_name for record in result.tool_calls], failed,
            sum(record.map_reduced for record in result.tool_calls), len(result.sources),
            preview(result.error or ""), preview(result.summary),
        )
        return result
