"""Dependency container so the graph can be built with real or fake agents."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.admin_agent import AdminAgent
from app.agents.final_agent import FinalAgent
from app.agents.orchestrator.agent import OrchestratorAgent
from app.agents.react_agent import ReActAgent
from app.agents.sufficiency import SufficiencyAgent
from app.constants import AGENT_CRM, AGENT_PORTFOLIO
from app.prompts.crm_agent import CRM_AGENT_SYSTEM_PROMPT
from app.prompts.portfolio_agent import PORTFOLIO_AGENT_SYSTEM_PROMPT
from app.tools.registry import get_tool_registry


def _portfolio_agent() -> ReActAgent:
    return ReActAgent(AGENT_PORTFOLIO, PORTFOLIO_AGENT_SYSTEM_PROMPT, get_tool_registry().portfolio)


def _crm_agent() -> ReActAgent:
    return ReActAgent(AGENT_CRM, CRM_AGENT_SYSTEM_PROMPT, get_tool_registry().crm)


@dataclass
class GraphDependencies:
    orchestrator: OrchestratorAgent = field(default_factory=OrchestratorAgent)
    portfolio: ReActAgent = field(default_factory=_portfolio_agent)
    crm: ReActAgent = field(default_factory=_crm_agent)
    admin: AdminAgent = field(default_factory=AdminAgent)
    sufficiency: SufficiencyAgent = field(default_factory=SufficiencyAgent)
    final: FinalAgent = field(default_factory=FinalAgent)
