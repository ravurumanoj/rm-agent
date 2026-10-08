"""Text renderings of entities, plans, agent results and sufficiency verdicts for prompts."""

from __future__ import annotations

from typing import Any

from app.schemas.agent_io import AgentResult
from app.schemas.orchestrator import ExecutionPlan, ExtractedEntities
from app.schemas.sufficiency import RefetchInstruction, SufficiencyVerdict


def format_entities(entities: ExtractedEntities) -> str:
    lines = []
    if entities.client_id:
        lines.append(f"- client_id: {entities.client_id}")
    if entities.portfolio_ids:
        lines.append(f"- portfolio_ids: {', '.join(entities.portfolio_ids)}")
    if entities.time_range:
        lines.append(f"- time_range: {entities.time_range}")
    if entities.topics:
        lines.append(f"- topics: {', '.join(entities.topics)}")
    return "\n".join(lines) or "- none specified"


def format_metadata_block(metadata: dict[str, Any]) -> str:
    """Request context (client and active portfolios) as prompt text."""
    lines = []
    if metadata.get("client_id"):
        lines.append(f"- client_id: {metadata['client_id']}")
    portfolios = [
        str(item.get("id") if isinstance(item, dict) else item)
        for item in metadata.get("active_portfolios") or []
    ]
    if portfolios:
        lines.append(f"- active portfolios: {', '.join(portfolios)}")
    return "\n".join(lines) or "- none"


def format_plan(plan: ExecutionPlan | None) -> str:
    if plan is None or plan.is_empty:
        return "(no plan)"
    return "\n".join(
        f"- stage {index} / {step.agent}: {step.objective}"
        for index, stage in enumerate(plan.stages, start=1)
        for step in stage.steps
    )


def format_results_overview(results: dict[str, AgentResult], *, max_chars: int = 600) -> str:
    if not results:
        return "(nothing returned yet)"
    lines = []
    for agent, result in results.items():
        summary = " ".join(result.summary.split())
        if len(summary) > max_chars:
            summary = summary[: max_chars - 3] + "..."
        lines.append(f"- {agent} [{result.status}, attempt {result.attempt}]: {summary or '(empty)'}")
    return "\n".join(lines)


def format_refetch_instructions(instructions: list[RefetchInstruction]) -> str:
    if not instructions:
        return "(none)"
    return "\n".join(
        f"- {item.agent}: missing {item.missing_information}; guidance: {item.instruction}"
        + (f"; suggested tools: {', '.join(item.suggested_tools)}" if item.suggested_tools else "")
        for item in instructions
    )


def format_sufficiency_report(verdict: SufficiencyVerdict | None) -> str:
    if verdict is None:
        return "(no sufficiency report)"
    lines = [f"Verdict: {verdict.verdict}", f"Reasoning: {verdict.reasoning}"]
    for item in verdict.assessments:
        issues = f" - {'; '.join(item.issues)}" if item.issues else ""
        lines.append(f"Assessment {item.agent}: {'adequate' if item.adequate else 'inadequate'}{issues}")
    if verdict.grounding_issues:
        lines.append("Grounding issues: " + "; ".join(verdict.grounding_issues))
    if verdict.refetch_instructions:
        lines.append("Re-fetch instructions:\n" + format_refetch_instructions(verdict.refetch_instructions))
    if verdict.human_question is not None:
        human = verdict.human_question
        lines.append(f"Needed from RM: {human.question} (fields: {', '.join(human.missing_fields) or 'n/a'}; {human.reason})")
    if verdict.data_gaps:
        lines.append("Data gaps: " + "; ".join(verdict.data_gaps))
    return "\n".join(lines)
