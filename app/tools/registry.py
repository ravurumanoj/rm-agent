"""Single place that decides which tool implementations the agents use."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.tools.admin_client import UniqueAdminAgentClient
from app.tools.base import ExternalAgentClient, ToolProvider
from app.tools.crm_tools import PlaceholderCrmToolProvider
from app.tools.portfolio_tools import PlaceholderPortfolioToolProvider


@dataclass(frozen=True)
class ToolRegistry:
    portfolio: ToolProvider
    crm: ToolProvider
    admin: ExternalAgentClient


@lru_cache(maxsize=1)
def get_tool_registry() -> ToolRegistry:
    """Return placeholder integrations; swap these for MCP/Unique-backed ones when ready."""
    return ToolRegistry(
        portfolio=PlaceholderPortfolioToolProvider(),
        crm=PlaceholderCrmToolProvider(),
        admin=UniqueAdminAgentClient(),
    )
