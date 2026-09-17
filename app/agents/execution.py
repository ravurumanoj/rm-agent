import asyncio
from typing import Any

from app.agents.portfolio_insights import portfolio_insights_agent
from app.agents.relationship_intelligence import relationship_intelligence_agent
from app.constants import (
    AGENT_CRM,
    AGENT_PORTFOLIO,
    EXEC_MODE_SEQUENTIAL,
    ROUTE_BOTH,
    ROUTE_CRM_ONLY,
    ROUTE_PORTFOLIO_ONLY,
    SEQUENTIAL_HANDOFF_MAX_LEN,
)
from app.schemas.citations import CitationChunk
from app.schemas.internal import AgentState
from app.services.citations import CitationManager, coerce_citation_chunk
from app.services.tracing import operation_span, record_exception
from app.utils.logger import logger


_AGENT_REGISTRY = {
    AGENT_PORTFOLIO: (portfolio_insights_agent, "portfolio_output", "Portfolio"),
    AGENT_CRM: (relationship_intelligence_agent, "crm_output", "CRM"),
}


def has_effective_external_tool_outputs(tool_outputs: list[dict[str, Any]]) -> bool:
    """True if externally supplied tool_outputs contain usable (non-error) data."""
    if not tool_outputs:
        return False
    for item in tool_outputs:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("chunks"), list) and bool(item.get("chunks")):
            logger.debug("[EXEC] external_tool_outputs_effective via chunks tool=%s", item.get("tool"))
            return True
        result = item.get("result")
        if isinstance(result, dict) and result.get("error"):
            result = None
        if result not in (None, {}, []):
            logger.debug("[EXEC] external_tool_outputs_effective via result tool=%s", item.get("tool"))
            return True
        content = item.get("content")
        if content not in (None, "", {}, []):
            logger.debug("[EXEC] external_tool_outputs_effective via content tool=%s", item.get("tool"))
            return True
    logger.debug("[EXEC] external_tool_outputs_not_effective count=%s", len(tool_outputs))
    return False


# Backward-compatible internal alias.
_has_effective_legacy_tool_outputs = has_effective_external_tool_outputs


def _has_effective_tool_results(output: dict[str, Any] | None) -> bool:
    def _result_has_data(value: Any) -> bool:
        if isinstance(value, dict):
            if value.get("error"):
                return False

            chunks = value.get("chunks")
            if isinstance(chunks, list) and bool(chunks):
                return True

            nested = value.get("results_by_portfolio_id")
            if isinstance(nested, dict):
                return any(_result_has_data(item) for item in nested.values())

            if value.get("data") not in (None, {}, []):
                return True

            return False
        return value is not None

    results = (output or {}).get("tool_results")
    if not isinstance(results, dict) or not results:
        return False

    for value in results.values():
        if _result_has_data(value):
            return True
    return False


def _normalize_tool_chunks(tool_outputs: list[dict[str, Any]]) -> list[CitationChunk]:
    chunks: list[CitationChunk] = []
    for item in tool_outputs:
        raw_chunks = item.get("chunks", [])
        if not isinstance(raw_chunks, list):
            continue
        for raw in raw_chunks:
            if not isinstance(raw, dict):
                continue
            try:
                chunks.append(coerce_citation_chunk(raw))
            except Exception:
                continue
    return chunks


def targets_for_route(route: str) -> set[str]:
    if route == ROUTE_PORTFOLIO_ONLY:
        return {AGENT_PORTFOLIO}
    if route == ROUTE_CRM_ONLY:
        return {AGENT_CRM}
    if route == ROUTE_BOTH:
        return {AGENT_PORTFOLIO, AGENT_CRM}
    return set()


def _handoff_context(producer_label: str, output: dict[str, Any]) -> str:
    results = (output or {}).get("tool_results") or {}
    if not results:
        return ""
    lines = [
        f"Context from the {producer_label} agent. Use it to target what you retrieve:",
    ]
    for tool_name, result in results.items():
        text = str(result)
        if len(text) > SEQUENTIAL_HANDOFF_MAX_LEN:
            text = text[:SEQUENTIAL_HANDOFF_MAX_LEN] + "..."
        lines.append(f"- {tool_name}: {text}")
    return "\n".join(lines)


def _flatten_outputs_to_tool_items(outputs: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key, domain in (("portfolio_output", "portfolio"), ("crm_output", "crm")):
        data = outputs.get(key) or {}
        results = data.get("tool_results") or {}
        chunks = data.get("chunks") or []
        if not isinstance(results, dict):
            continue
        for tool_name, result in results.items():
            items.append(
                {
                    "domain": domain,
                    "tool": tool_name,
                    "result": result,
                    "chunks": chunks if isinstance(chunks, list) else [],
                }
            )
    return items


def _build_citation_references(tool_outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    manager = CitationManager(start_index=1)
    chunks = _normalize_tool_chunks(tool_outputs)
    references = manager.register_chunks(chunks)
    return [
        {
            "source_number": ref.source_number,
            "marker": ref.marker,
            "content_id": ref.content_id,
            "title": ref.title,
            "snippet": ref.snippet,
            "uri": ref.uri,
            "metadata": ref.metadata,
        }
        for ref in references
    ]


async def _run_parallel(state: AgentState, run_targets: set[str], replan_instruction: str = "") -> dict[str, Any]:
    with operation_span(
        "agent.execution.parallel",
        kind="CHAIN",
        attributes={"app.target_count": len(run_targets), "app.targets": sorted(run_targets)},
    ) as span:
        logger.info("[EXEC] mode=parallel targets=%s", sorted(run_targets))
        try:
            names = [name for name in [AGENT_PORTFOLIO, AGENT_CRM] if name in run_targets]
            tasks = []
            for name in names:
                agent, _key, _label = _AGENT_REGISTRY[name]
                tasks.append(agent.collect_data(state, extra_context=replan_instruction))

            outputs: dict[str, Any] = {}
            results = await asyncio.gather(*tasks)
            for name, result in zip(names, results):
                _agent, key, _label = _AGENT_REGISTRY[name]
                outputs[key] = result
            if span is not None:
                span.set_attribute("app.execution.success", True)
            return outputs
        except Exception as exc:
            if span is not None:
                span.set_attribute("app.execution.success", False)
            record_exception(span, exc)
            raise


async def _run_sequential(state: AgentState, producer: str, replan_instruction: str = "") -> dict[str, Any]:
    with operation_span(
        "agent.execution.sequential",
        kind="CHAIN",
        attributes={"app.producer": producer},
    ) as span:
        logger.info("[EXEC] mode=sequential producer=%s", producer)
        try:
            consumer = AGENT_CRM if producer == AGENT_PORTFOLIO else AGENT_PORTFOLIO
            prod_agent, prod_key, prod_label = _AGENT_REGISTRY[producer]
            cons_agent, cons_key, _ = _AGENT_REGISTRY[consumer]

            producer_output = await prod_agent.collect_data(state, extra_context=replan_instruction)
            handoff = _handoff_context(prod_label, producer_output)
            consumer_extra = (replan_instruction + "\n\n" + handoff).strip() if handoff else replan_instruction
            consumer_output = await cons_agent.collect_data(state, extra_context=consumer_extra)
            if span is not None:
                span.set_attribute("app.execution.success", True)
            return {prod_key: producer_output, cons_key: consumer_output}
        except Exception as exc:
            if span is not None:
                span.set_attribute("app.execution.success", False)
            record_exception(span, exc)
            raise


async def execute_agents(
    state: AgentState,
    *,
    route: str | None = None,
    execution_mode: str = "",
    producer: str = "",
    targets: set[str] | None = None,
    replan_instruction: str = "",
) -> dict:
    """Dispatch portfolio/CRM collectors using parallel or sequential mode."""
    active_route = route or state.get("route", "")
    logger.info(
        "[EXEC] execute_started route=%s mode=%s producer=%s",
        active_route,
        execution_mode,
        producer,
    )

    # Only request-supplied tool_outputs bypass execution; internal flattened
    # results (written back into state["tool_outputs"] after each attempt) must
    # never be mistaken for external input, or replan retries would just replay
    # the previous (possibly insufficient) result instead of re-fetching.
    legacy_tool_outputs = state.get("external_tool_outputs", [])
    if (
        isinstance(legacy_tool_outputs, list)
        and legacy_tool_outputs
        and _has_effective_legacy_tool_outputs(legacy_tool_outputs)
    ):
        logger.info("[EXEC] using_legacy_tool_outputs count=%s", len(legacy_tool_outputs))
        return {
            "tool_outputs": legacy_tool_outputs,
            "citation_references": _build_citation_references(legacy_tool_outputs),
        }

    run_targets = targets if targets is not None else targets_for_route(active_route)
    if not run_targets:
        logger.info("[EXEC] no_targets_for_route route=%s", active_route)
        return {"tool_outputs": [], "citation_references": []}

    both_present = {AGENT_PORTFOLIO, AGENT_CRM} <= run_targets
    if (
        active_route == ROUTE_BOTH
        and execution_mode == EXEC_MODE_SEQUENTIAL
        and producer in _AGENT_REGISTRY
        and both_present
    ):
        logger.debug("[EXEC] dispatch_mode=sequential targets=%s producer=%s", sorted(run_targets), producer)
        outputs = await _run_sequential(state, producer, replan_instruction=replan_instruction)
    else:
        logger.debug("[EXEC] dispatch_mode=parallel targets=%s", sorted(run_targets))
        outputs = await _run_parallel(state, run_targets, replan_instruction=replan_instruction)

    flattened = _flatten_outputs_to_tool_items(outputs)
    logger.debug(
        "[EXEC] flattened_tool_items=%s",
        [
            {"domain": item.get("domain"), "tool": item.get("tool"), "chunk_count": len(item.get("chunks") or [])}
            for item in flattened
        ],
    )
    logger.info(
        "[EXEC] execute_completed route=%s tool_items=%s",
        active_route,
        len(flattened),
    )
    return {
        **outputs,
        "tool_outputs": flattened,
        "citation_references": _build_citation_references(flattened),
    }

def evaluate_sufficiency_for_route(
    route: str,
    portfolio_output: dict[str, Any] | None,
    crm_output: dict[str, Any] | None,
) -> tuple[bool, str, set[str]]:
    needs_portfolio = route in (ROUTE_PORTFOLIO_ONLY, ROUTE_BOTH)
    needs_crm = route in (ROUTE_CRM_ONLY, ROUTE_BOTH)

    missing_desc: list[str] = []
    missing_targets: set[str] = set()

    portfolio_has_results = bool((portfolio_output or {}).get("tool_results"))
    portfolio_effective = _has_effective_tool_results(portfolio_output) if portfolio_has_results else False
    if needs_portfolio and not portfolio_has_results:
        missing_desc.append("portfolio data")
        missing_targets.add(AGENT_PORTFOLIO)
    elif needs_portfolio and not portfolio_effective:
        missing_desc.append("portfolio data")
        missing_targets.add(AGENT_PORTFOLIO)

    crm_has_results = bool((crm_output or {}).get("tool_results"))
    crm_effective = _has_effective_tool_results(crm_output) if crm_has_results else False
    if needs_crm and not crm_has_results:
        missing_desc.append("CRM data")
        missing_targets.add(AGENT_CRM)
    elif needs_crm and not crm_effective:
        missing_desc.append("CRM data")
        missing_targets.add(AGENT_CRM)

    logger.debug(
        "[EXEC] sufficiency_check route=%s needs_portfolio=%s portfolio_has_results=%s portfolio_effective=%s "
        "needs_crm=%s crm_has_results=%s crm_effective=%s missing_targets=%s",
        route,
        needs_portfolio,
        portfolio_has_results,
        portfolio_effective,
        needs_crm,
        crm_has_results,
        crm_effective,
        sorted(missing_targets),
    )

    return (not missing_targets, " and ".join(missing_desc), missing_targets)


def evaluate_sufficiency(state: AgentState) -> bool:
    """Backward-compatible sufficiency helper for legacy callers."""
    route = state.get("route", "")
    sufficient, _missing, _targets = evaluate_sufficiency_for_route(
        route,
        state.get("portfolio_output") if isinstance(state.get("portfolio_output"), dict) else {},
        state.get("crm_output") if isinstance(state.get("crm_output"), dict) else {},
    )
    return sufficient
