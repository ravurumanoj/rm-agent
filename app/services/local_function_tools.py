from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_PORTFOLIO_FILE = _DATA_DIR / "portfolio.json"
_CRM_FILE = _DATA_DIR / "crm.json"

_ALLOWED_CRM_CHANNELS = {"phone", "video_call", "meeting"}


@lru_cache(maxsize=1)
def _portfolio_records() -> list[dict[str, Any]]:
    if not _PORTFOLIO_FILE.exists():
        return []
    with _PORTFOLIO_FILE.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, list) else []


@lru_cache(maxsize=1)
def _crm_records() -> list[dict[str, Any]]:
    if not _CRM_FILE.exists():
        return []
    with _CRM_FILE.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, list) else []


def _by_portfolio_id(records: list[dict[str, Any]], portfolio_id: str) -> dict[str, Any] | None:
    pid = (portfolio_id or "").strip()
    if not pid:
        return None
    for item in records:
        if str(item.get("portfolio_id") or "").strip() == pid:
            return item
    return None


def _chunk(*, chunk_id: str, title: str, content_obj: Any, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    content = json.dumps(content_obj, ensure_ascii=True, separators=(",", ":"))
    return {
        "id": chunk_id,
        "title": title,
        "content": content,
        "url": f"local://{chunk_id}",
        "metadata": metadata or {},
    }


class PortfolioIdArgs(BaseModel):
    portfolio_id: str = Field(..., description="Portfolio ID, e.g. 1111, 2222")


class PortfolioHoldingsArgs(PortfolioIdArgs):
    top_n: int = Field(10, ge=1, le=50, description="Number of holdings to return")


class CrmHistoryArgs(PortfolioIdArgs):
    limit: int = Field(5, ge=1, le=50, description="Number of latest interactions")


def get_portfolio_summary(portfolio_id: str) -> dict[str, Any]:
    record = _by_portfolio_id(_portfolio_records(), portfolio_id)
    if not record:
        return {"error": f"Portfolio not found for portfolio_id={portfolio_id}", "chunks": []}

    payload = {
        "portfolio_id": portfolio_id,
        "account_details": record.get("account_details", {}),
        "customer_profile": record.get("customer_profile", {}),
        "portfolio_summary": record.get("portfolio_summary", {}),
        "asset_allocation": record.get("asset_allocation", {}),
        "performance_metrics": record.get("performance_metrics", {}),
    }
    return {
        "portfolio_id": portfolio_id,
        "data": payload,
        "chunks": [
            _chunk(
                chunk_id=f"portfolio-summary-{portfolio_id}",
                title=f"Portfolio Summary {portfolio_id}",
                content_obj=payload,
                metadata={"domain": "portfolio", "portfolio_id": portfolio_id, "tool": "get_portfolio_summary"},
            )
        ],
    }


def get_portfolio_holdings(portfolio_id: str, top_n: int = 10) -> dict[str, Any]:
    record = _by_portfolio_id(_portfolio_records(), portfolio_id)
    if not record:
        return {"error": f"Portfolio not found for portfolio_id={portfolio_id}", "chunks": []}

    holdings = record.get("holdings") if isinstance(record.get("holdings"), list) else []
    sliced = holdings[:top_n]
    payload = {"portfolio_id": portfolio_id, "top_n": top_n, "holdings": sliced}
    return {
        "portfolio_id": portfolio_id,
        "data": payload,
        "chunks": [
            _chunk(
                chunk_id=f"portfolio-holdings-{portfolio_id}-n{top_n}",
                title=f"Portfolio Holdings {portfolio_id}",
                content_obj=payload,
                metadata={"domain": "portfolio", "portfolio_id": portfolio_id, "tool": "get_portfolio_holdings"},
            )
        ],
    }


def get_crm_interactions(portfolio_id: str, limit: int = 5) -> dict[str, Any]:
    record = _by_portfolio_id(_crm_records(), portfolio_id)
    if not record:
        return {"error": f"CRM record not found for portfolio_id={portfolio_id}", "chunks": []}

    history = record.get("conversation_history") if isinstance(record.get("conversation_history"), list) else []
    filtered = [item for item in history if str(item.get("channel") or "").lower() in _ALLOWED_CRM_CHANNELS]
    latest = filtered[:limit]

    payload = {
        "portfolio_id": portfolio_id,
        "customer_profile": record.get("customer_profile", {}),
        "relationship_manager": record.get("relationship_manager", {}),
        "interactions": latest,
    }
    return {
        "portfolio_id": portfolio_id,
        "data": payload,
        "chunks": [
            _chunk(
                chunk_id=f"crm-interactions-{portfolio_id}-n{limit}",
                title=f"CRM Interactions {portfolio_id}",
                content_obj=payload,
                metadata={"domain": "crm", "portfolio_id": portfolio_id, "tool": "get_crm_interactions"},
            )
        ],
    }


def get_crm_followups(portfolio_id: str) -> dict[str, Any]:
    record = _by_portfolio_id(_crm_records(), portfolio_id)
    if not record:
        return {"error": f"CRM record not found for portfolio_id={portfolio_id}", "chunks": []}

    history = record.get("conversation_history") if isinstance(record.get("conversation_history"), list) else []
    followups = [
        {
            "conversation_id": item.get("conversation_id"),
            "date": item.get("date"),
            "topic": item.get("topic"),
            "follow_up_required": item.get("follow_up_required"),
            "follow_up_date": item.get("follow_up_date"),
            "follow_up_action": item.get("follow_up_action"),
            "action_items": item.get("action_items") or [],
        }
        for item in history
        if bool(item.get("follow_up_required"))
    ]

    payload = {
        "portfolio_id": portfolio_id,
        "alerts": record.get("alerts") or [],
        "followups": followups,
        "service_requests": record.get("service_requests") or [],
    }
    return {
        "portfolio_id": portfolio_id,
        "data": payload,
        "chunks": [
            _chunk(
                chunk_id=f"crm-followups-{portfolio_id}",
                title=f"CRM Followups {portfolio_id}",
                content_obj=payload,
                metadata={"domain": "crm", "portfolio_id": portfolio_id, "tool": "get_crm_followups"},
            )
        ],
    }


def get_available_portfolio_ids() -> list[str]:
    return [str(item.get("portfolio_id") or "").strip() for item in _portfolio_records() if str(item.get("portfolio_id") or "").strip()]


def build_default_agent_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    base = dict(metadata or {})

    if not isinstance(base.get("portfolio_tools"), list) or not base.get("portfolio_tools"):
        base["portfolio_tools"] = [
            StructuredTool.from_function(
                func=get_portfolio_summary,
                name="get_portfolio_summary",
                description="Fetch account, summary, allocation, and performance data for one portfolio_id.",
                args_schema=PortfolioIdArgs,
            ),
            StructuredTool.from_function(
                func=get_portfolio_holdings,
                name="get_portfolio_holdings",
                description="Fetch holdings for one portfolio_id. Use top_n when user asks top holdings.",
                args_schema=PortfolioHoldingsArgs,
            ),
        ]

    if not isinstance(base.get("crm_tools"), list) or not base.get("crm_tools"):
        base["crm_tools"] = [
            StructuredTool.from_function(
                func=get_crm_interactions,
                name="get_crm_interactions",
                description="Fetch recent call/meeting interactions for one portfolio_id.",
                args_schema=CrmHistoryArgs,
            ),
            StructuredTool.from_function(
                func=get_crm_followups,
                name="get_crm_followups",
                description="Fetch follow-up actions, alerts, and service request status for one portfolio_id.",
                args_schema=PortfolioIdArgs,
            ),
        ]

    if not base.get("active_portfolios"):
        base["active_portfolios"] = [{"id": pid} for pid in get_available_portfolio_ids()]

    return base
