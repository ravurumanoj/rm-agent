from __future__ import annotations

from typing import Any

from app.schemas.internal import AgentState
from app.utils.logger import logger


def _has_effective_external_tool_outputs(tool_outputs: list[dict[str, Any]]) -> bool:
    if not tool_outputs:
        return False
    for item in tool_outputs:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("chunks"), list) and bool(item.get("chunks")):
            return True
        result = item.get("result")
        if isinstance(result, dict) and result.get("error"):
            result = None
        if result not in (None, {}, []):
            return True
        content = item.get("content")
        if content not in (None, "", {}, []):
            return True
    return False


def extract_injected_domain_results(
    state: AgentState,
    *,
    domain_name: str,
    default_tool_name: str,
    accepted_name_prefixes: tuple[str, ...],
) -> dict[str, Any]:
    """Normalize injected tool outputs for one domain.

    Reads only externally supplied tool_outputs (from the API request), never
    the internal "tool_outputs" working key that execute_agents overwrites
    with each attempt's own flattened result — otherwise a collector would
    treat its own previous (possibly empty/error) output as pre-injected
    data on every replan retry and never actually re-fetch.
    """
    raw = state.get("external_tool_outputs") or []
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

    if tool_results and _has_effective_external_tool_outputs(raw):
        logger.info(
            "[TOOL_INJECT] domain=%s using_external_tool_outputs tools=%s chunks=%s",
            domain_name,
            tools_called,
            len(chunks),
        )
    elif tool_results:
        logger.info(
            "[TOOL_INJECT] domain=%s ignoring_ineffective_external_tool_outputs tools=%s",
            domain_name,
            tools_called,
        )
        tool_results = {}
        tools_called = []
        chunks = []
    else:
        logger.debug("[TOOL_INJECT] domain=%s no_external_tool_outputs; proceeding to normal collection", domain_name)

    return {"tool_results": tool_results, "tools_called": tools_called, "chunks": chunks}
