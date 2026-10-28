"""Where this app applies the generic map-reduce component, and with which goal.

Level 1 (agent): large tool results are consolidated before they enter the agent's conversation, for the
agent's objective. Level 2 (evidence): after all agents ran, oversized evidence blocks are consolidated for the
RM's request. Both keep citation markers, flag unverified figures and report unprocessed parts.
"""

from __future__ import annotations

from typing import Any, Callable

from app.components.map_reduce import MapReduceConfig, MapReduceResult, MapReduceSummarizer
from app.components.react.models import ToolOutput
from app.components.react.tool_executor import ExecutedCall
from app.config import settings
from app.observability import SpanKind, SpanName, set_attributes, start_span
from app.utils.logger import logger

LEVEL_AGENT = "agent"
LEVEL_EVIDENCE = "evidence"


def build_summarizer(llm: Any) -> MapReduceSummarizer:
    return MapReduceSummarizer(
        llm,
        MapReduceConfig(
            trigger_tokens=settings.MAP_REDUCE_TRIGGER_TOKENS,
            chunk_tokens=settings.MAP_REDUCE_CHUNK_TOKENS,
            max_parallel=settings.MAP_REDUCE_MAX_PARALLEL,
            max_depth=settings.MAP_REDUCE_MAX_DEPTH,
            chars_per_token=settings.MAP_REDUCE_CHARS_PER_TOKEN,
        ),
    )


async def traced_reduce(
    summarizer: MapReduceSummarizer, content: str, *, goal: str, context: str, level: str, target: str
) -> MapReduceResult:
    """One reduction inside a step.map_reduce span, so its cost and effect are visible per query in Arize AX."""
    with start_span(
        SpanName.step("map_reduce"),
        kind=SpanKind.CHAIN,
        attributes={"app.map_reduce.level": level, "app.map_reduce.target": target},
    ) as span:
        result = await summarizer.reduce(content, goal=goal, context=context, force=True)
        set_attributes(
            span,
            {
                "app.map_reduce.input_tokens": result.input_tokens,
                "app.map_reduce.output_tokens": result.output_tokens,
                "app.map_reduce.llm_used": result.reduced,
                "app.map_reduce.parts": result.parts,
                "app.map_reduce.depth": result.depth,
                "app.map_reduce.failed_parts": result.failed_parts,
                "app.map_reduce.unverified_figures": len(result.unverified_figures),
            },
        )
        logger.info(
            "[MAP_REDUCE] level=%s target=%s tokens=%s->%s parts=%s failed=%s unverified=%s",
            level, target, result.input_tokens, result.output_tokens, result.parts, result.failed_parts,
            len(result.unverified_figures),
        )
        return result


# --- level 1: tool results inside one agent run ------------------------------------------------------------------


async def reduce_large_observations(
    executed: list[ExecutedCall], conversation_chars: int, *, llm: Any, goal: str, context: str
) -> list[ExecutedCall]:
    """Shrink this step's tool results, largest first, while one is too large or the run's context is filling.

    "Filling": the conversation so far plus this step's results exceed MAP_REDUCE_AGENT_CONTEXT_SHARE of the
    window. Results under MAP_REDUCE_MIN_TOKENS are never touched.
    """
    summarizer = build_summarizer(llm)
    budget = int(settings.MAP_REDUCE_CONTEXT_WINDOW_TOKENS * settings.MAP_REDUCE_AGENT_CONTEXT_SHARE)
    sizes = {
        index: summarizer.tokens(item.observation)
        for index, item in enumerate(executed)
        if item.record is not None and item.record.output is not None and not item.record.no_data
    }
    used = summarizer.tokens("x" * conversation_chars) + sum(sizes.values())
    result = list(executed)
    for index in sorted(sizes, key=sizes.get, reverse=True):
        size = sizes[index]
        if size < settings.MAP_REDUCE_MIN_TOKENS or (size <= summarizer.config.trigger_tokens and used <= budget):
            break
        item = executed[index]
        reduced = await traced_reduce(
            summarizer, item.record.output.content, goal=goal, context=context, level=LEVEL_AGENT, target=item.record.tool_name
        )
        result[index] = _replace_output(item, reduced)
        used -= size - summarizer.tokens(result[index].observation)
    return result


def agent_goal(objective: str) -> str:
    return (
        f"Agent objective: {objective}\n"
        "Keep every value, item and detail this objective needs, including inputs for any calculation it implies."
    )


def _replace_output(item: ExecutedCall, reduced: MapReduceResult) -> ExecutedCall:
    output = item.record.output
    record = item.record.model_copy(
        update={
            "output": ToolOutput(content=reduced.text, sources=output.sources),
            "reduced_from_tokens": reduced.input_tokens,
            "map_reduced": reduced.reduced,
        }
    )
    titles = "; ".join(source.title or source.source_id for source in output.sources)
    observation = f"{reduced.text}\n(sources: {titles})" if titles else reduced.text
    return ExecutedCall(item.call_id, observation, record)


# --- level 2: merged evidence of all agents ----------------------------------------------------------------------


async def consolidate_evidence(
    evidence: dict[str, str],
    *,
    get_llm: Callable[[], Any],
    resolved_query: str,
    objectives: dict[str, str],
    context: str,
) -> tuple[dict[str, str], int]:
    """Shrink the largest agent evidence blocks until the total fits the trigger; returns (evidence, reductions).

    Blocks stay per agent, so the sufficiency check can still assess each agent; [sourceN] markers are kept.
    get_llm is called only when a reduction is needed, so small evidence never creates an LLM client.
    """
    sizer = build_summarizer(None)
    sizes = {agent: sizer.tokens(text) for agent, text in evidence.items()}
    total = sum(sizes.values())
    if total <= sizer.config.trigger_tokens:
        return evidence, 0

    summarizer = build_summarizer(get_llm())

    consolidated = dict(evidence)
    reductions = 0
    for agent in sorted(sizes, key=sizes.get, reverse=True):
        if total <= summarizer.config.trigger_tokens or sizes[agent] < settings.MAP_REDUCE_MIN_TOKENS:
            break
        header, _, body = evidence[agent].partition("\n")
        goal = (
            f"RM request to answer: {resolved_query}\n"
            f"This evidence comes from the {agent} agent, whose task was: {objectives.get(agent, '(not recorded)')}\n"
            "Keep every value, item, calculation input and source marker ([sourceN]) needed to answer the request "
            "in full; keep failed-call and unavailable-data notes."
        )
        reduced = await traced_reduce(summarizer, body, goal=goal, context=context, level=LEVEL_EVIDENCE, target=agent)
        consolidated[agent] = f"{header}\n{reduced.text}"
        reductions += int(reduced.reduced)
        total -= sizes[agent] - summarizer.tokens(consolidated[agent])
    return consolidated, reductions
