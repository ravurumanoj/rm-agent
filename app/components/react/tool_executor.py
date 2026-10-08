"""Parallel, bounded and failure-tolerant execution of the tool calls a model requested."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool

from app.components.react.models import ReActConfig, ToolCallRecord, ToolOutput
from app.components.text_preview import preview

log = logging.getLogger(__name__)


@dataclass
class ExecutedCall:
    """Observation handed back to the model, plus the audit record (None for skipped duplicates)."""

    call_id: str
    observation: str
    record: ToolCallRecord | None


def coerce_tool_output(raw: Any) -> ToolOutput:
    """Normalize whatever a tool returned into a ToolOutput."""
    if isinstance(raw, ToolOutput):
        return raw
    if isinstance(raw, dict) and "content" in raw:
        try:
            return ToolOutput.model_validate(raw)
        except ValueError:
            pass
    return ToolOutput(content=raw if isinstance(raw, str) else json.dumps(raw, default=str))


def _signature(name: str, args: dict[str, Any]) -> str:
    return f"{name}:{json.dumps(args, sort_keys=True, default=str)}"


async def _run_one(
    call: dict[str, Any],
    tool_map: dict[str, BaseTool],
    iteration: int,
    semaphore: asyncio.Semaphore,
    timeout: float,
) -> ExecutedCall:
    name, call_id = str(call.get("name") or ""), str(call.get("id") or call.get("name") or "")
    args = call.get("args") if isinstance(call.get("args"), dict) else {}

    tool = tool_map.get(name)
    log.debug("tool call started name=%s iteration=%s args=%s", name, iteration, preview(args))
    if tool is None:
        error = f"Unknown tool '{name}'. Available tools: {', '.join(sorted(tool_map))}."
        output = None
        duration_ms = 0
    else:
        started = time.perf_counter()
        async with semaphore:
            try:
                output, error = coerce_tool_output(await asyncio.wait_for(tool.ainvoke(args), timeout)), None
            except asyncio.TimeoutError:
                output, error = None, f"Tool timed out after {timeout:.0f}s."
            except Exception as exc:  # a tool failure must not abort the loop
                output, error = None, f"{type(exc).__name__}: {exc}"
        duration_ms = int((time.perf_counter() - started) * 1000)

    if error:
        log.warning("tool call failed name=%s duration_ms=%s error=%s", name, duration_ms, error)
    else:
        log.info(
            "tool call ok name=%s duration_ms=%s sources=%s result=%s",
            name, duration_ms, len(output.sources), preview(output.content),
        )
    record = ToolCallRecord(
        iteration=iteration, tool_name=name, arguments=args, output=output, error=error, duration_ms=duration_ms
    )
    if output is None:
        return ExecutedCall(call_id, f"ERROR: {error}", record)
    titles = "; ".join(s.title or s.source_id for s in output.sources)
    return ExecutedCall(call_id, f"{output.content}\n(sources: {titles})" if titles else output.content, record)


async def execute_tool_calls(
    calls: list[dict[str, Any]],
    tool_map: dict[str, BaseTool],
    *,
    iteration: int,
    seen_signatures: set[str],
    config: ReActConfig,
) -> list[ExecutedCall]:
    """Run requested calls concurrently; identical repeats are skipped with a hint to the model."""
    semaphore = asyncio.Semaphore(max(1, config.max_parallel_tool_calls))
    pending: list[asyncio.Future[ExecutedCall] | ExecutedCall] = []

    for call in calls:
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        signature = _signature(str(call.get("name") or ""), args)
        if signature in seen_signatures:
            log.info("tool call skipped duplicate name=%s args=%s", call.get("name"), preview(args))
            pending.append(
                ExecutedCall(
                    str(call.get("id") or call.get("name") or ""),
                    "SKIPPED: identical call already made; reuse the earlier observation or change the arguments.",
                    None,
                )
            )
            continue
        seen_signatures.add(signature)
        pending.append(
            asyncio.ensure_future(_run_one(call, tool_map, iteration, semaphore, config.tool_timeout_seconds))
        )

    return [item if isinstance(item, ExecutedCall) else await item for item in pending]
