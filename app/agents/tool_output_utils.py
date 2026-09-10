from __future__ import annotations

from typing import Any

from app.schemas.internal import AgentState


def extract_injected_domain_results(
    state: AgentState,
    *,
    domain_name: str,
    default_tool_name: str,
    accepted_name_prefixes: tuple[str, ...],
) -> dict[str, Any]:
    """Normalize injected tool outputs for one domain."""
    raw = state.get("tool_outputs") or []
    tool_results: dict[str, Any] = {}
    tools_called: list[str] = []
    chunks: list[dict[str, Any]] = []

    for item in raw:
        if not isinstance(item, dict):
            continue

        domain = str(item.get("domain") or "").lower()
        tool_name = str(item.get("tool") or item.get("name") or default_tool_name)
        tool_name_lower = tool_name.lower()

        if domain and domain != domain_name:
            continue

        if domain == "" and accepted_name_prefixes and not any(
            tool_name_lower.startswith(prefix) for prefix in accepted_name_prefixes
        ):
            continue

        tool_results[tool_name] = item.get("result") or item.get("content") or item
        if tool_name not in tools_called:
            tools_called.append(tool_name)

        raw_chunks = item.get("chunks")
        if isinstance(raw_chunks, list):
            chunks.extend([chunk for chunk in raw_chunks if isinstance(chunk, dict)])

    return {"tool_results": tool_results, "tools_called": tools_called, "chunks": chunks}
