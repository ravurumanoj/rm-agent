"""Tool-calling sub-agent: one class configured per domain (portfolio, CRM) with its own prompt and tools."""

from __future__ import annotations

from app.agents.agent_result import build_agent_result
from app.agents.base import BaseAgent
from app.agents.formatting import format_entities, format_metadata_block
from app.components.react import ReActConfig, ReActRunner
from app.config import settings
from app.prompts.react import (
    AGENT_TASK_USER_TEMPLATE,
    EXTRA_INSTRUCTION_BLOCK_TEMPLATE,
    HANDOFF_BLOCK_TEMPLATE,
    REACT_FORCE_FINAL_MESSAGE,
)
from app.schemas.agent_io import AgentResult, AgentTask
from app.services.tracing import operation_span, record_exception
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
        )

    def build_user_prompt(self, task: AgentTask) -> str:
        handoff = HANDOFF_BLOCK_TEMPLATE.format(handoff_context=task.handoff_context) if task.handoff_context else ""
        extra = (
            EXTRA_INSTRUCTION_BLOCK_TEMPLATE.format(attempt=task.attempt, extra_instruction=task.extra_instruction)
            if task.extra_instruction
            else ""
        )
        return AGENT_TASK_USER_TEMPLATE.format(
            objective=task.objective,
            user_query=task.user_query,
            context_block=format_metadata_block(task.metadata),
            entities_block=format_entities(task.entities),
            handoff_block=handoff,
            extra_instruction_block=extra,
        )

    async def run(self, task: AgentTask) -> AgentResult:
        label = self.agent_id.upper()
        logger.info("[%s] run_started attempt=%s objective=%s", label, task.attempt, preview(task.objective))
        user_prompt = self.build_user_prompt(task)
        logger.debug("[%s] user_prompt=%s", label, preview(user_prompt))
        with operation_span(
            f"agent.{self.agent_id}.react", kind="AGENT", attributes={"app.agent": self.agent_id, "app.attempt": task.attempt}
        ) as span:
            try:
                runner = ReActRunner(
                    self.llm,
                    self._tool_provider.get_tools(),
                    system_prompt=self._system_prompt.format(max_iterations=self._config.max_iterations),
                    config=self._config,
                )
                outcome = await runner.run(user_prompt)
            except Exception as exc:  # an agent crash degrades to an error result instead of aborting the graph
                logger.exception("[%s] run_crashed", label)
                record_exception(span, exc)
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
        logger.info(
            "[%s] run_completed status=%s tool_calls=%s iterations=%s terminated=%s summary=%s",
            label, result.status, len(result.tool_calls), result.iterations, result.terminated_reason,
            preview(result.summary),
        )
        if result.error:
            logger.warning("[%s] run_error error=%s", label, preview(result.error))
        return result
