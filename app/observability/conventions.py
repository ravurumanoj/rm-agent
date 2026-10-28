"""Naming conventions for every span, attribute and log field, so traces read the same in any project.

Span names are "<category>.<name>", lowercase with underscores:
    query                      root span of one user query (always a new trace: one trace per query)
    http.<route>               an inbound HTTP request
    step.<name>                an internal step outside the graph, e.g. step.load_history
    node.<graph_node>          one step of the workflow graph, e.g. node.sufficiency_check
    agent.<agent>              one sub-agent run, e.g. agent.portfolio
    tool.<tool>                one tool / MCP / external-agent call, e.g. tool.get_portfolio_summary
    llm.<caller>               one model call, named after its caller, e.g. llm.orchestrator.routing

Span kinds are the OpenInference kinds Arize AX understands (LLM, TOOL, AGENT, CHAIN, ...).
Custom attributes live under "app." so they never collide with OpenInference or OpenTelemetry keys.
"""

from __future__ import annotations

from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes


class SpanKind:
    """OpenInference span kinds (Arize AX groups, filters and costs spans by these)."""

    CHAIN = OpenInferenceSpanKindValues.CHAIN.value
    AGENT = OpenInferenceSpanKindValues.AGENT.value
    TOOL = OpenInferenceSpanKindValues.TOOL.value
    LLM = OpenInferenceSpanKindValues.LLM.value
    RETRIEVER = OpenInferenceSpanKindValues.RETRIEVER.value
    GUARDRAIL = OpenInferenceSpanKindValues.GUARDRAIL.value
    EVALUATOR = OpenInferenceSpanKindValues.EVALUATOR.value


class SpanName:
    """Builders for span names; use these instead of free-form strings."""

    QUERY = "query"

    @staticmethod
    def http(route: str) -> str:
        return f"http.{_slug(route)}"

    @staticmethod
    def step(name: str) -> str:
        return f"step.{_slug(name)}"

    @staticmethod
    def node(node: str) -> str:
        return f"node.{_slug(node)}"

    @staticmethod
    def agent(agent: str) -> str:
        return f"agent.{_slug(agent)}"

    @staticmethod
    def tool(tool: str) -> str:
        return f"tool.{_slug(tool)}"

    @staticmethod
    def llm(caller: str) -> str:
        return f"llm.{caller or 'call'}"


class Attr:
    """Attribute keys. Identity keys are stamped on every span; the rest are set where they happen."""

    # Identity: ties spans, logs and metrics of one query together (sessions use the OpenInference keys).
    CORRELATION_ID = "app.correlation_id"
    SESSION_ID = SpanAttributes.SESSION_ID
    USER_ID = SpanAttributes.USER_ID
    METADATA = SpanAttributes.METADATA

    # Workflow position. Arize AX builds the agent graph from the OpenInference graph.node.* keys and agent.name.
    GRAPH_NODE = "app.graph_node"
    GRAPH_NODE_ID = SpanAttributes.GRAPH_NODE_ID
    GRAPH_NODE_NAME = SpanAttributes.GRAPH_NODE_NAME
    GRAPH_NODE_PARENT_ID = SpanAttributes.GRAPH_NODE_PARENT_ID
    GRAPH_STEP = "app.graph.step"  # start order of the graph nodes within the query (the root is 0)
    GRAPH_STAGE = "app.graph.stage"  # plan stage an agent ran in; agents of one stage run in parallel
    AGENT_NAME = SpanAttributes.AGENT_NAME
    LLM_CALL = "app.llm_call"

    # Agent decisions
    DECISION_PHASE = "app.decision.phase"
    DECISION_INTENT = "app.decision.intent"
    DECISION_AGENTS = "app.decision.agents"
    DECISION_EXECUTION_MODE = "app.decision.execution_mode"
    SUFFICIENCY_VERDICT = "app.decision.sufficiency_verdict"
    REPLAN_COUNT = "app.decision.replan_count"

    # Reliability
    ERROR_TYPE = "error.type"
    ERROR_KIND = "app.error.kind"
    ERROR_WHERE = "app.error.where"
    ERROR_HTTP_STATUS = "http.response.status_code"
    ERROR_HTTP_BODY = "app.error.http_body"
    RETRY_ATTEMPTS = "app.retry.attempts"
    STATUS = "app.status"

    # Cost inputs that are not standard OpenInference keys
    TOKENS_ESTIMATED = "app.token_count.estimated"

    # Query outcome and quality signals (on the query span; Arize AX charts and monitors filter on these)
    OUTCOME = "app.query.outcome"
    PARTIAL_ANSWER = "app.query.partial_answer"
    TOOL_CALL_COUNT = "app.query.tool_call_count"
    TOOL_FAILURE_COUNT = "app.query.tool_failure_count"
    RETRY_COUNT = "app.query.retry_count"
    MAP_REDUCE_COUNT = "app.query.map_reduce_count"
    CITATION_COUNT = "app.quality.citation_count"
    CITATION_COVERAGE = "app.quality.citation_coverage"
    GUARDRAIL_TRIGGERED = "app.quality.guardrail_triggered"
    OUT_OF_SCOPE = "app.quality.out_of_scope"


class LogField:
    """Fields on every JSON log line; correlation_id and trace_id cross-link logs with Arize AX traces."""

    TIMESTAMP = "timestamp"
    LEVEL = "level"
    LOGGER = "logger"
    MESSAGE = "message"
    SERVICE = "service"
    STREAM = "log_stream"
    CORRELATION_ID = "correlation_id"
    SESSION_ID = "session_id"
    USER_ID = "user_id"
    TRACE_ID = "trace_id"
    SPAN_ID = "span_id"


class LogStream:
    """Two separate log streams: route them to different ELK indices with their own retention and access."""

    OPERATIONAL = "operational"
    AUDIT = "audit"


def _slug(value: str) -> str:
    return "_".join(str(value).strip().lower().replace("-", " ").replace("/", " ").split()) or "unnamed"
