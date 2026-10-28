"""Parallel, bounded and failure-tolerant execution of the tool calls a model requested."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool

from app.components.react.models import ReActConfig, ToolCallRecord, ToolOutput
from app.components.resilience import (
    CIRCUIT_OPEN,
    ErrorKind,
    RetryStats,
    classify_error,
    get_circuit_breaker,
    is_timeout,
    retry_async,
)
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


def _argument_hint(tool: BaseTool, *, max_chars: int = 1500) -> str:
    """The tool's expected arguments, appended to an error so the model can correct its next call."""
    try:
        hint = json.dumps(tool.args, default=str)
    except Exception:  # dynamically loaded tools may expose an unusual schema
        return ""
    return hint if len(hint) <= max_chars else hint[: max_chars - 3] + "..."


async def _run_one(
    call: dict[str, Any],
    tool_map: dict[str, BaseTool],
    iteration: int,
    semaphore: asyncio.Semaphore,
    config: ReActConfig,
) -> ExecutedCall:
    """One tool call inside its tracing span when the host configured one (see ReActConfig.tool_span).

    The span opens once a parallel slot is free, so its latency is the call itself, not the wait for a slot.
    """
    name = str(call.get("name") or "")
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    async with semaphore:
        with config.tool_span(name, args) if config.tool_span else nullcontext() as span:
            executed = await _execute_one(call, tool_map, iteration, config)
            if config.on_tool_result is not None and executed.record is not None:
                config.on_tool_result(span, executed.record)
            return executed


async def _execute_one(
    call: dict[str, Any],
    tool_map: dict[str, BaseTool],
    iteration: int,
    config: ReActConfig,
) -> ExecutedCall:
    name, call_id = str(call.get("name") or ""), str(call.get("id") or call.get("name") or "")
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    timeout = config.tool_timeout_seconds

    tool = tool_map.get(name)
    output: ToolOutput | None = None
    error: str | None = None
    error_kind: str | None = None
    stats = RetryStats(attempts=0)
    started = time.perf_counter()
    breaker = (
        get_circuit_breaker(
            config.breaker_key,
            failure_threshold=config.breaker_failure_threshold,
            cooldown_seconds=config.breaker_cooldown_seconds,
        )
        if config.breaker_key
        else None
    )

    if tool is None:
        error, error_kind = f"Unknown tool '{name}'. Available tools: {', '.join(sorted(tool_map))}.", ErrorKind.NON_RETRYABLE.value
    elif breaker is not None and not breaker.allow():
        error, error_kind = str(breaker.unavailable_error()), CIRCUIT_OPEN
    else:

        async def _call() -> ToolOutput:
            return coerce_tool_output(await asyncio.wait_for(tool.ainvoke(args), timeout))

        try:
            # Tools only read data, so retrying a transient failure is idempotent and safe.
            output = await retry_async(
                _call,
                max_retries=config.max_retries,
                base_delay=config.retry_base_delay,
                max_delay=config.retry_max_delay,
                jitter_ratio=config.retry_jitter_ratio,
                retry_on_timeout=config.retry_on_timeout,
                default_kind=ErrorKind.NON_RETRYABLE,  # unknown tool errors: let the model fix its arguments
                label=f"tool {name}",
                stats=stats,
            )
        except Exception as exc:  # a tool failure must not abort the loop
            kind = classify_error(exc, default=ErrorKind.NON_RETRYABLE)
            error = f"Tool timed out after {timeout:.0f}s." if is_timeout(exc) else f"{type(exc).__name__}: {exc}"
            error_kind = kind.value
        if breaker is not None:
            # Only technical failures count against the server; a rejected request proves it is reachable.
            breaker.record_failure() if error_kind == ErrorKind.RETRYABLE.value else breaker.record_success()
    duration_ms = int((time.perf_counter() - started) * 1000)

    if error:
        log.warning(
            "[TOOL] call_failed name=%s iteration=%s kind=%s attempts=%s duration_ms=%s args=%s error=%s",
            name, iteration, error_kind, stats.attempts, duration_ms, preview(args), error,
        )
    else:
        log.info(
            "[TOOL] call_ok name=%s iteration=%s attempts=%s duration_ms=%s chars=%s sources=%s args=%s result=%s",
            name, iteration, stats.attempts, duration_ms, len(output.content), len(output.sources), preview(args),
            preview(output.content),
        )
    record = ToolCallRecord(
        iteration=iteration,
        tool_name=name,
        arguments=args,
        output=output,
        error=error,
        error_kind=error_kind,
        attempts=max(1, stats.attempts),
        duration_ms=duration_ms,
    )
    if output is None:
        return ExecutedCall(call_id, _error_observation(name, tool, error or "", error_kind, stats.attempts), record)
    if record.no_data:
        return ExecutedCall(call_id, "NO DATA: the call succeeded but the source returned nothing for these arguments.", record)
    titles = "; ".join(s.title or s.source_id for s in output.sources)
    return ExecutedCall(call_id, f"{output.content}\n(sources: {titles})" if titles else output.content, record)


def _error_observation(name: str, tool: BaseTool | None, error: str, error_kind: str | None, attempts: int) -> str:
    """Tell the model whether to fix its call or to stop and report the data as unavailable."""
    if error_kind in (ErrorKind.RETRYABLE.value, CIRCUIT_OPEN):
        tried = f" after {attempts} attempts" if attempts > 1 else ""
        return (
            f"ERROR (temporarily unavailable{tried}): {error}\n"
            "Do not call this tool again in this run; report the data as temporarily unavailable."
        )
    hint = _argument_hint(tool) if tool is not None else ""
    return f"ERROR: {error}" + (f"\nExpected arguments for {name}: {hint}" if hint else "")


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
            log.warning("[TOOL] duplicate_skipped name=%s args=%s (identical call already made)", call.get("name"), preview(args))
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
            asyncio.ensure_future(_run_one(call, tool_map, iteration, semaphore, config))
        )

    return [item if isinstance(item, ExecutedCall) else await item for item in pending]
