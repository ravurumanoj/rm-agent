"""Placeholder portfolio tools. Replace the coroutines with AAA MCP calls when the integration lands."""

from __future__ import annotations

from langchain_core.tools import BaseTool, StructuredTool

from app.schemas.tool_args import PortfolioHoldingsArgs, PortfolioIdArgs, PortfolioPerformanceArgs
from app.tools.base import dummy_tool_output


async def _get_portfolio_summary(portfolio_id: str) -> dict:
    return dummy_tool_output(
        f"portfolio-summary:{portfolio_id}", source_prefix="aaa-summary", title=f"Portfolio {portfolio_id} summary"
    )


async def _get_portfolio_holdings(portfolio_id: str, top_n: int = 10) -> dict:
    return dummy_tool_output(
        f"portfolio-holdings:{portfolio_id}:{top_n}",
        source_prefix="aaa-holdings",
        title=f"Portfolio {portfolio_id} top {top_n} holdings",
    )


async def _get_portfolio_performance(portfolio_id: str, period: str = "YTD") -> dict:
    return dummy_tool_output(
        f"portfolio-performance:{portfolio_id}:{period}",
        source_prefix="aaa-performance",
        title=f"Portfolio {portfolio_id} performance ({period})",
    )


class PlaceholderPortfolioToolProvider:
    """Stand-in for the AAA MCP server tools."""

    def get_tools(self) -> list[BaseTool]:
        return [
            StructuredTool.from_function(
                coroutine=_get_portfolio_summary,
                name="get_portfolio_summary",
                description="Account details, current value, invested value, unrealized P&L and asset allocation for one portfolio.",
                args_schema=PortfolioIdArgs,
            ),
            StructuredTool.from_function(
                coroutine=_get_portfolio_holdings,
                name="get_portfolio_holdings",
                description="Largest holdings of one portfolio with weights and positions.",
                args_schema=PortfolioHoldingsArgs,
            ),
            StructuredTool.from_function(
                coroutine=_get_portfolio_performance,
                name="get_portfolio_performance",
                description="Returns, benchmark comparison and risk metrics of one portfolio over a period.",
                args_schema=PortfolioPerformanceArgs,
            ),
        ]
