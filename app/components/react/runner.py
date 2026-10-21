"""Generic bounded ReAct loop: reason, call tools in parallel, observe, repeat.

Usage:
    runner = ReActRunner(llm, tools, system_prompt="...", config=ReActConfig(max_iterations=4))
    outcome = await runner.run("user task")
`llm` needs `bind_tools(tools)` and `ainvoke(messages)` (any LangChain chat model).
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from app.components.llm_call_context import llm_call
from app.components.messages import extract_text
from app.components.react.models import (
    TERMINATED_FINAL_ANSWER,
    TERMINATED_LLM_ERROR,
    TERMINATED_MAX_ITERATIONS,
    ReActConfig,
    ReActOutcome,
    ReActStep,
)
from app.components.react.tool_executor import execute_tool_calls
from app.components.text_preview import preview

log = logging.getLogger(__name__)


class ReActRunner:
    def __init__(self, llm: Any, tools: list[BaseTool], *, system_prompt: str, config: ReActConfig | None = None) -> None:
        self._config = config or ReActConfig()
        self._llm = llm.bind_tools(tools)
        self._tool_map = {tool.name: tool for tool in tools}
        self._system_prompt = system_prompt

    async def run(self, user_prompt: str) -> ReActOutcome:
        messages: list[BaseMessage] = [SystemMessage(content=self._system_prompt), HumanMessage(content=user_prompt)]
        outcome = ReActOutcome(terminated_reason=TERMINATED_MAX_ITERATIONS)
        seen: set[str] = set()
        log.debug("[REACT] start tools=%s max_iterations=%s", sorted(self._tool_map), self._config.max_iterations)

        for iteration in range(1, max(1, self._config.max_iterations) + 1):
            try:
                with llm_call(f"step_{iteration}"):  # llm.portfolio_agent.step_1, step_2, ...
                    ai = await self._llm.ainvoke(messages)
            except Exception as exc:
                log.warning("[REACT] llm_failed iteration=%s error=%s: %s; stopping the loop", iteration, type(exc).__name__, exc)
                outcome.error, outcome.terminated_reason = str(exc), TERMINATED_LLM_ERROR
                return outcome

            outcome.iterations = iteration
            thought = extract_text(ai.content).strip()
            calls = list(getattr(ai, "tool_calls", None) or [])
            if not calls:
                log.debug("[REACT] final_answer iteration=%s answer=%s", iteration, preview(thought))
                outcome.final_text, outcome.terminated_reason = thought, TERMINATED_FINAL_ANSWER
                return outcome

            log.debug(
                "[REACT] step iteration=%s tool_calls=%s thought=%s",
                iteration, [str(c.get("name") or "") for c in calls], preview(thought),
            )
            outcome.steps.append(
                ReActStep(iteration=iteration, thought=thought, tool_names=[str(c.get("name") or "") for c in calls])
            )
            messages.append(ai)
            executed = await execute_tool_calls(
                calls, self._tool_map, iteration=iteration, seen_signatures=seen, config=self._config
            )
            if self._config.reduce_observations is not None:
                # Large results are consolidated before they enter the conversation and fill the context window.
                conversation_chars = sum(len(extract_text(message.content)) for message in messages)
                executed = await self._config.reduce_observations(executed, conversation_chars)
            for item in executed:
                messages.append(ToolMessage(content=item.observation, tool_call_id=item.call_id))
                if item.record is not None:
                    outcome.records.append(item.record)

        try:
            with llm_call("forced_summary"):
                final = await self._llm.ainvoke([*messages, HumanMessage(content=self._config.force_final_message)])
            outcome.final_text = extract_text(final.content).strip()
            log.warning(
                "[REACT] step_limit_reached iterations=%s tool_calls=%s; forced a final summary answer=%s",
                outcome.iterations, len(outcome.records), preview(outcome.final_text),
            )
        except Exception as exc:
            log.warning("[REACT] forced_summary_failed iterations=%s error=%s: %s", outcome.iterations, type(exc).__name__, exc)
            outcome.error, outcome.terminated_reason = str(exc), TERMINATED_LLM_ERROR
        return outcome
