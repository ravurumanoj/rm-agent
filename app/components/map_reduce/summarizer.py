"""Goal-driven map-reduce over content too large for one LLM call.

    summarizer = MapReduceSummarizer(llm, MapReduceConfig(trigger_tokens=40_000, chunk_tokens=24_000))
    result = await summarizer.reduce(raw_tool_output, goal="YTD return and top 10 holdings of 1111",
                                     context="portfolio_ids: 1111\\nperiod: YTD")
    result.text       # consolidated extract, ready for later steps
    result.reduced    # False when the content already fit (returned unchanged, no LLM call)

Steps: structure-aware verbatim chunks -> parallel map (one extract per chunk, partial calculations included) ->
tree reduce (merge and recompute, recursing until it fits, depth-capped) -> figure check. The input is never
transformed before the LLM reads it. Nothing is dropped silently: failed chunks are reported, and a failed merge
keeps the extracts as they are.
`llm` is any object with `async ainvoke(messages)` returning a message with `.content`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.components.llm_call_context import llm_call
from app.components.map_reduce.chunking import (
    DEFAULT_CHARS_PER_TOKEN,
    dataset_head,
    estimate_tokens,
    split_into_chunks,
)
from app.components.map_reduce.fidelity import find_unverified_figures
from app.components.map_reduce.prompts import (
    EXTRACT_TEMPLATE,
    MAP_SYSTEM_PROMPT,
    MAP_USER_TEMPLATE,
    REDUCE_SYSTEM_PROMPT,
    REDUCE_USER_TEMPLATE,
)
from app.components.messages import extract_text

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MapReduceConfig:
    trigger_tokens: int = 40_000  # content above this is map-reduced
    chunk_tokens: int = 24_000  # per map call; smaller chunks extract more reliably than near-full windows
    max_parallel: int = 4  # concurrent map/reduce calls
    max_depth: int = 3  # reduce levels before extracts are kept side by side instead of merged again
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN


@dataclass
class MapReduceResult:
    text: str
    reduced: bool  # True when the LLM map-reduce ran; False when the content fit and was returned unchanged
    input_tokens: int
    output_tokens: int
    parts: int = 0
    depth: int = 0
    failed_parts: list[int] = field(default_factory=list)
    unverified_figures: list[str] = field(default_factory=list)


class MapReduceSummarizer:
    def __init__(self, llm: Any, config: MapReduceConfig | None = None) -> None:
        self._llm = llm
        self.config = config or MapReduceConfig()
        self._semaphore = asyncio.Semaphore(max(1, self.config.max_parallel))

    def tokens(self, text: str) -> int:
        return estimate_tokens(text, self.config.chars_per_token)

    def needs_reduction(self, text: str) -> bool:
        return self.tokens(text) > self.config.trigger_tokens

    async def reduce(self, content: str, *, goal: str, context: str = "", force: bool = False) -> MapReduceResult:
        """Consolidate content for the goal; force=True map-reduces even when it already fits."""
        input_tokens = self.tokens(content)
        if not force and not self.needs_reduction(content):
            return MapReduceResult(content, False, input_tokens, input_tokens)

        chunks = split_into_chunks(content, max_tokens=self.config.chunk_tokens, chars_per_token=self.config.chars_per_token)
        head = dataset_head(content)
        log.debug("[MAP_REDUCE] started input_tokens=%s parts=%s chunk_tokens=%s", input_tokens, len(chunks), self.config.chunk_tokens)
        extracts = await asyncio.gather(
            *(
                self._map(chunk, number, len(chunks), goal, context, head if number > 1 else "")
                for number, chunk in enumerate(chunks, start=1)
            )
        )
        failed = [number for number, extract in enumerate(extracts, start=1) if extract is None]
        usable = [extract for extract in extracts if extract]
        if not usable:
            log.error("[MAP_REDUCE] all_parts_failed parts=%s; keeping the original content", len(chunks))
            return MapReduceResult(content, False, input_tokens, input_tokens, len(chunks), 0, failed)

        text, depth = await self._reduce_tree(usable, goal, context, failed, depth=1)
        unverified = find_unverified_figures(text, content)
        if unverified:
            text += (
                "\n\n### Unverified figures\nNot found verbatim in the source data; treat as unconfirmed: "
                + ", ".join(unverified)
            )
        (log.warning if failed or unverified else log.debug)(
            "[MAP_REDUCE] completed parts=%s failed_parts=%s depth=%s tokens=%s->%s unverified_figures=%s",
            len(chunks), failed, depth, input_tokens, self.tokens(text), unverified,
        )
        return MapReduceResult(text, True, input_tokens, self.tokens(text), len(chunks), depth, failed, unverified)

    async def _map(self, chunk: str, number: int, total: int, goal: str, context: str, head: str) -> str | None:
        prompt = MAP_USER_TEMPLATE.format(
            goal=goal,
            context=context or "(none)",
            dataset_start=head or "(this part is the start of the dataset)",
            part=number,
            parts=total,
            chunk=chunk,
        )
        try:
            with llm_call(f"map_reduce.map.part_{number}"):
                return await self._call(MAP_SYSTEM_PROMPT, prompt)
        except Exception:
            log.warning("[MAP_REDUCE] map_failed part=%s/%s chunk_chars=%s; reported as unprocessed", number, total, len(chunk), exc_info=True)
            return None

    async def _reduce_tree(
        self, extracts: list[str], goal: str, context: str, failed: list[int], *, depth: int
    ) -> tuple[str, int]:
        """Merge extracts; groups are merged first when they do not fit one call. Never drops an extract."""
        if len(extracts) == 1 and not failed:
            return extracts[0], depth - 1
        groups = self._group(extracts)
        if len(groups) == 1:
            return await self._merge(groups[0], goal, context, failed, depth, 1), depth
        # Stop merging when it can no longer shrink anything (every extract alone fills a call) or at the cap.
        if depth >= self.config.max_depth or all(len(group) == 1 for group in groups):
            log.warning("[MAP_REDUCE] merge_stopped depth=%s/%s extracts=%s; keeping them side by side", depth, self.config.max_depth, len(extracts))
            side_by_side = "\n\n".join(extracts)
            if failed:
                side_by_side += "\n\n### Not available\nUnprocessed parts: " + ", ".join(f"part {n}" for n in failed)
            return side_by_side, depth
        merged = await asyncio.gather(
            *(self._merge(group, goal, context, [], depth, number) for number, group in enumerate(groups, start=1))
        )
        return await self._reduce_tree(list(merged), goal, context, failed, depth=depth + 1)

    async def _merge(
        self, extracts: list[str], goal: str, context: str, failed: list[int], depth: int, group: int
    ) -> str:
        blocks = "\n\n".join(
            EXTRACT_TEMPLATE.format(number=number, total=len(extracts), text=text)
            for number, text in enumerate(extracts, start=1)
        )
        prompt = REDUCE_USER_TEMPLATE.format(
            goal=goal,
            context=context or "(none)",
            unprocessed=", ".join(f"part {number}" for number in failed) or "(none)",
            extracts=blocks,
        )
        try:
            with llm_call(f"map_reduce.reduce.level_{depth}.group_{group}"):
                return await self._call(REDUCE_SYSTEM_PROMPT, prompt)
        except Exception:
            # Keep every extract rather than lose data; the next steps can still read them.
            log.warning("[MAP_REDUCE] merge_failed level=%s group=%s extracts=%s; keeping them unmerged", depth, group, len(extracts), exc_info=True)
            return "\n\n".join(extracts)

    def _group(self, extracts: list[str]) -> list[list[str]]:
        """Consecutive extracts packed into groups that each fit one reduce call."""
        groups: list[list[str]] = [[]]
        size = 0
        for extract in extracts:
            tokens = self.tokens(extract)
            if groups[-1] and size + tokens > self.config.chunk_tokens:
                groups.append([])
                size = 0
            groups[-1].append(extract)
            size += tokens
        return groups

    async def _call(self, system_prompt: str, user_prompt: str) -> str:
        async with self._semaphore:
            reply = await self._llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
        text = extract_text(reply.content).strip()
        if not text:
            raise ValueError("empty map-reduce reply")
        return text
