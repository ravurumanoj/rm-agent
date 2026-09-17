from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from app.agents.base import BaseAgent
from app.agents.tool_output_utils import extract_injected_domain_results
from app.config import settings
from app.prompts.relationship_intelligence import (
    CRM_TOOL_COLLECTION_SUFFIX,
    RELATIONSHIP_INTELLIGENCE_SYSTEM_PROMPT,
    RELATIONSHIP_INTELLIGENCE_USER_TEMPLATE,
)
from app.schemas.internal import AgentState
from app.services.observability import operation_span, record_exception
from app.utils.logger import logger


class RelationshipIntelligenceAgent(BaseAgent):
    """CRM collector adapted from wealth app tool-calling pattern."""

    @staticmethod
    def _portfolio_arg_keys(tool: Any, args: dict[str, Any]) -> tuple[list[str], list[str]]:
        names = set(args.keys())

        schema = getattr(tool, "args_schema", None)
        model_fields = getattr(schema, "model_fields", None)
        if isinstance(model_fields, dict):
            names.update(model_fields.keys())
        legacy_fields = getattr(schema, "__fields__", None)
        if isinstance(legacy_fields, dict):
            names.update(legacy_fields.keys())

        singular: list[str] = []
        plural: list[str] = []
        for name in sorted(names):
            lowered = name.lower()
            if "portfolio" not in lowered or "id" not in lowered:
                continue
            if "ids" in lowered:
                plural.append(name)
            else:
                singular.append(name)
        return singular, plural

    @classmethod
    def _build_scoped_tool_args(
        cls,
        tool: Any,
        tool_args: dict[str, Any],
        selected_portfolio_ids: list[str],
    ) -> list[tuple[str, dict[str, Any]]]:
        if not selected_portfolio_ids:
            return [("", tool_args)]

        singular_keys, plural_keys = cls._portfolio_arg_keys(tool, tool_args)
        if singular_keys:
            key = singular_keys[0]
            calls: list[tuple[str, dict[str, Any]]] = []
            for portfolio_id in selected_portfolio_ids:
                scoped = deepcopy(tool_args)
                scoped[key] = portfolio_id
                calls.append((portfolio_id, scoped))
            return calls

        if plural_keys:
            key = plural_keys[0]
            calls: list[tuple[str, dict[str, Any]]] = []
            for portfolio_id in selected_portfolio_ids:
                scoped = deepcopy(tool_args)
                scoped[key] = [portfolio_id]
                calls.append((portfolio_id, scoped))
            return calls

        return [("", tool_args)]

    @classmethod
    async def _invoke_tool_with_scoping(
        cls,
        tool: Any,
        raw_args: dict[str, Any],
        selected_portfolio_ids: list[str],
    ) -> Any:
        scoped_calls = cls._build_scoped_tool_args(tool, raw_args, selected_portfolio_ids)
        if len(scoped_calls) == 1:
            _pid, scoped_args = scoped_calls[0]
            return await tool.ainvoke(scoped_args)

        by_id: dict[str, Any] = {}
        merged_chunks: list[dict[str, Any]] = []
        for portfolio_id, scoped_args in scoped_calls:
            item = await tool.ainvoke(scoped_args)
            by_id[portfolio_id] = item
            if isinstance(item, dict) and isinstance(item.get("chunks"), list):
                merged_chunks.extend([c for c in item.get("chunks", []) if isinstance(c, dict)])
        return {
            "portfolio_ids": selected_portfolio_ids,
            "results_by_portfolio_id": by_id,
            "chunks": merged_chunks,
        }

    @staticmethod
    def _has_effective_results(tool_results: dict[str, Any]) -> bool:
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

        if not isinstance(tool_results, dict) or not tool_results:
            return False
        for value in tool_results.values():
            if _result_has_data(value):
                return True
        return False

    async def _apply_deterministic_fallback(
        self,
        tools: list[Any],
        selected_portfolio_ids: list[str],
        tool_results: dict[str, Any],
        tools_called: list[str],
        chunks: list[dict[str, Any]],
    ) -> None:
        logger.warning(
            "[CRM] deterministic_fallback_triggered selected_ids=%s tools=%s",
            selected_portfolio_ids,
            [getattr(t, "name", "") for t in tools],
        )
        for tool in tools:
            name = getattr(tool, "name", "")
            if not name:
                continue
            try:
                result = await self._invoke_tool_with_scoping(tool, {}, selected_portfolio_ids)
            except Exception as exc:
                result = {"error": str(exc)}
            tool_results[name] = result
            if name not in tools_called:
                tools_called.append(name)
            if isinstance(result, dict) and isinstance(result.get("chunks"), list):
                chunks.extend([c for c in result.get("chunks", []) if isinstance(c, dict)])
            has_error = isinstance(result, dict) and bool(result.get("error"))
            logger.debug(
                "[CRM] fallback_tool_call name=%s error=%s chunk_count=%s",
                name,
                result.get("error") if has_error else None,
                len(result.get("chunks") or []) if isinstance(result, dict) else 0,
            )

    async def collect_data(self, state: AgentState, *, extra_context: str = "") -> dict[str, Any]:
        injected = extract_injected_domain_results(
            state,
            domain_name="crm",
            default_tool_name="crm_tool",
            accepted_name_prefixes=("crm", "meeting"),
        )
        if injected["tool_results"]:
            return injected

        metadata = state.get("metadata") or {}
        tools = metadata.get("crm_tools") or []
        if not isinstance(tools, list) or not tools:
            logger.warning("[CRM] no_crm_tools_configured_in_metadata")
            return {"tool_results": {}, "tools_called": [], "chunks": []}

        user_msg = (state.get("user_message") or "").strip()
        selected_portfolio_ids = [str(x).strip() for x in (state.get("selected_portfolio_ids") or []) if str(x).strip()]
        logger.info(
            "[CRM] collect_data_started selected_ids=%s available_tools=%s extra_context=%r",
            selected_portfolio_ids,
            [getattr(t, "name", "") for t in tools],
            extra_context[:200] if extra_context else "",
        )

        additional_context = "Portfolio-centric retrieval mode for CRM insights."
        if selected_portfolio_ids:
            additional_context += (
                "\nIf a CRM tool accepts portfolio_id/portfolio_ids, use only: "
                + ", ".join(selected_portfolio_ids)
            )
        if extra_context:
            additional_context += f"\n\n{extra_context}"

        prompt = RELATIONSHIP_INTELLIGENCE_USER_TEMPLATE.format(
            user_message=user_msg + CRM_TOOL_COLLECTION_SUFFIX,
            additional_context=additional_context,
        )

        llm_with_tools = self.llm.bind_tools(tools)
        messages = [
            SystemMessage(content=RELATIONSHIP_INTELLIGENCE_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]

        tool_results: dict[str, Any] = {}
        tools_called: list[str] = []
        chunks: list[dict[str, Any]] = []
        tool_map = {getattr(t, "name", ""): t for t in tools if getattr(t, "name", "")}

        for _ in range(max(1, settings.MCP_MAX_TOOL_ITERATIONS)):
            try:
                ai = await llm_with_tools.ainvoke(messages)
            except Exception as exc:
                logger.warning("[CRM] tool_planning_failed error=%s", exc)
                break
            if not getattr(ai, "tool_calls", None):
                logger.debug("[CRM] llm_requested_no_tool_calls")
                break
            logger.debug(
                "[CRM] llm_requested_tool_calls count=%s names=%s",
                len(ai.tool_calls),
                [tc.get("name") for tc in ai.tool_calls],
            )
            messages.append(ai)

            async def _call(tc: dict[str, Any]):
                name = tc.get("name", "")
                call_id = tc.get("id", "") or name
                tool = tool_map.get(name)
                if tool is None:
                    logger.warning("[CRM] tool_not_found name=%s", name)
                    return name, call_id, {"error": f"Tool '{name}' not found"}

                raw_args = tc.get("args") or {}
                if not isinstance(raw_args, dict):
                    raw_args = {}

                try:
                    with operation_span(
                        "tool.crm.invoke",
                        kind="TOOL",
                        attributes={
                            "tool.name": name,
                            "tool.call_id": call_id,
                            "app.selected_portfolio_count": len(selected_portfolio_ids),
                        },
                    ) as span:
                        result = await self._invoke_tool_with_scoping(tool, raw_args, selected_portfolio_ids)
                        if span is not None:
                            span.set_attribute("tool.success", not (isinstance(result, dict) and bool(result.get("error"))))
                    has_error = isinstance(result, dict) and bool(result.get("error"))
                    logger.debug(
                        "[CRM] tool_call name=%s raw_args=%s error=%s chunk_count=%s",
                        name,
                        raw_args,
                        result.get("error") if has_error else None,
                        len(result.get("chunks") or []) if isinstance(result, dict) else 0,
                    )
                    return name, call_id, result
                except Exception as exc:
                    with operation_span(
                        "tool.crm.invoke",
                        kind="TOOL",
                        attributes={"tool.name": name, "tool.call_id": call_id},
                    ) as span:
                        if span is not None:
                            span.set_attribute("tool.success", False)
                        record_exception(span, exc)
                    logger.warning("[CRM] tool_call_exception name=%s error=%s", name, exc)
                    return name, call_id, {"error": str(exc)}

            results = await asyncio.gather(*[_call(tc) for tc in ai.tool_calls])
            for name, call_id, result in results:
                tool_results[name] = result
                if name and name not in tools_called:
                    tools_called.append(name)
                if isinstance(result, dict) and isinstance(result.get("chunks"), list):
                    chunks.extend([c for c in result.get("chunks", []) if isinstance(c, dict)])
                messages.append(ToolMessage(content=str(result), tool_call_id=call_id))

        effective = self._has_effective_results(tool_results)
        logger.debug("[CRM] effective_results_check result=%s tool_results_keys=%s", effective, list(tool_results.keys()))
        if not effective:
            await self._apply_deterministic_fallback(
                tools,
                selected_portfolio_ids,
                tool_results,
                tools_called,
                chunks,
            )

        logger.info(
            "[CRM] collect_data_completed tools_called=%s chunk_count=%s",
            tools_called,
            len(chunks),
        )
        return {"tool_results": tool_results, "tools_called": tools_called, "chunks": chunks}


relationship_intelligence_agent = RelationshipIntelligenceAgent()
