from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from app.constants import CLARIFY_ASK


class AgentState(TypedDict, total=False):
    """Shared state threaded through every agent node."""

    messages: Annotated[list[BaseMessage], add_messages]
    session_id: str
    metadata: dict[str, Any]
    user_message: str
    history_block: str
    route: str
    execution_mode: str
    producer: str
    requested_portfolio_ids: list[str]
    known_portfolio_ids: list[str]
    selected_portfolio_ids: list[str]
    unmatched_portfolio_ids: list[str]
    needs_portfolio_clarification: bool
    portfolio_clarification_question: str
    tool_outputs: list[dict[str, Any]]
    external_tool_outputs: list[dict[str, Any]]
    portfolio_output: dict[str, Any]
    crm_output: dict[str, Any]
    citation_references: list[dict[str, Any]]
    cited_references: list[dict[str, Any]]
    evaluation_results: list[dict[str, Any]]
    needs_clarification: bool
    clarification_question: str
    replan_instruction: str
    retrieval_missing_sections: list[str]
    retry_count: int
    final_output: str


@dataclass
class ClarificationDecision:
    action: str
    question: Optional[str] = None

    @property
    def needs_clarification(self) -> bool:
        return self.action == CLARIFY_ASK and bool(self.question)


@dataclass
class RouteDecision:
    route: str
    execution_mode: Optional[str] = None
    producer: Optional[str] = None


@dataclass(frozen=True)
class TurnResult:
    reply: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    evaluations: list[dict[str, Any]] = field(default_factory=list)
