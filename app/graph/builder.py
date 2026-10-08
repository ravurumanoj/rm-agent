"""Assembles the multi-agent workflow graph.

START -> orchestrator -> {direct_reply | safe_decline | ask_human | stage_dispatch}
stage_dispatch -> (fan-out) {portfolio_agent | crm_agent | admin_agent} -> stage_join
stage_join -> stage_dispatch (next stage) | reduce_outputs -> sufficiency_check
sufficiency_check -> orchestrator (replan / ask human) | final_agent -> END
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.constants import (
    AGENT_TO_NODE,
    NODE_ADMIN_AGENT,
    NODE_ASK_HUMAN,
    NODE_CRM_AGENT,
    NODE_DIRECT_REPLY,
    NODE_FINAL_AGENT,
    NODE_ORCHESTRATOR,
    NODE_PORTFOLIO_AGENT,
    NODE_REDUCE_OUTPUTS,
    NODE_SAFE_DECLINE,
    NODE_STAGE_DISPATCH,
    NODE_STAGE_JOIN,
    NODE_SUFFICIENCY,
)
from app.graph.dependencies import GraphDependencies
from app.graph.edges import (
    fan_out_stage,
    route_after_orchestrator,
    route_after_stage_join,
    route_after_sufficiency,
)
from app.graph.nodes.answer_nodes import make_final_node, make_sufficiency_node, reduce_outputs_node
from app.graph.nodes.execution_nodes import make_agent_node, stage_dispatch_node, stage_join_node
from app.graph.nodes.orchestrator_node import make_orchestrator_node
from app.graph.nodes.terminal_nodes import ask_human_node, direct_reply_node, safe_decline_node
from app.graph.state import AgentNodeInput, GraphState

AGENT_NODES = tuple(AGENT_TO_NODE.values())


def build_graph(deps: GraphDependencies | None = None):
    deps = deps or GraphDependencies()
    graph = StateGraph(GraphState)

    graph.add_node(NODE_ORCHESTRATOR, make_orchestrator_node(deps.orchestrator))
    graph.add_node(NODE_DIRECT_REPLY, direct_reply_node)
    graph.add_node(NODE_SAFE_DECLINE, safe_decline_node)
    graph.add_node(NODE_ASK_HUMAN, ask_human_node)
    graph.add_node(NODE_STAGE_DISPATCH, stage_dispatch_node)
    graph.add_node(NODE_PORTFOLIO_AGENT, make_agent_node(deps.portfolio, NODE_PORTFOLIO_AGENT), input_schema=AgentNodeInput)
    graph.add_node(NODE_CRM_AGENT, make_agent_node(deps.crm, NODE_CRM_AGENT), input_schema=AgentNodeInput)
    graph.add_node(NODE_ADMIN_AGENT, make_agent_node(deps.admin, NODE_ADMIN_AGENT), input_schema=AgentNodeInput)
    graph.add_node(NODE_STAGE_JOIN, stage_join_node)
    graph.add_node(NODE_REDUCE_OUTPUTS, reduce_outputs_node)
    graph.add_node(NODE_SUFFICIENCY, make_sufficiency_node(deps.sufficiency))
    graph.add_node(NODE_FINAL_AGENT, make_final_node(deps.final))

    graph.add_edge(START, NODE_ORCHESTRATOR)
    graph.add_conditional_edges(
        NODE_ORCHESTRATOR,
        route_after_orchestrator,
        [NODE_DIRECT_REPLY, NODE_SAFE_DECLINE, NODE_ASK_HUMAN, NODE_STAGE_DISPATCH],
    )
    graph.add_conditional_edges(NODE_STAGE_DISPATCH, fan_out_stage, list(AGENT_NODES))
    for agent_node in AGENT_NODES:
        graph.add_edge(agent_node, NODE_STAGE_JOIN)
    graph.add_conditional_edges(
        NODE_STAGE_JOIN, route_after_stage_join, [NODE_STAGE_DISPATCH, NODE_REDUCE_OUTPUTS]
    )
    graph.add_edge(NODE_REDUCE_OUTPUTS, NODE_SUFFICIENCY)
    graph.add_conditional_edges(NODE_SUFFICIENCY, route_after_sufficiency, [NODE_ORCHESTRATOR, NODE_FINAL_AGENT])

    for terminal in (NODE_DIRECT_REPLY, NODE_SAFE_DECLINE, NODE_ASK_HUMAN, NODE_FINAL_AGENT):
        graph.add_edge(terminal, END)

    return graph.compile()


@lru_cache(maxsize=1)
def get_compiled_graph():
    return build_graph()


def render_graph_mermaid() -> str:
    """Mermaid source of the compiled workflow, for documentation."""
    return get_compiled_graph().get_graph().draw_mermaid()
