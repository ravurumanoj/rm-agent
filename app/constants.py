# Route/response constants shared across the app.

DOCS_URL = "/docs"
REDOC_URL = "/redoc"
OPENAPI_URL = "/openapi.json"

HEALTH_STATUS_HEALTHY = "healthy"
HEALTH_STATUS_DEGRADED = "degraded"

# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------
AGENT_PORTFOLIO = "portfolio"
AGENT_CRM = "crm"
AGENT_ADMIN = "admin"
VALID_AGENTS = frozenset({AGENT_PORTFOLIO, AGENT_CRM, AGENT_ADMIN})
AGENT_ORDER = (AGENT_PORTFOLIO, AGENT_CRM, AGENT_ADMIN)

AGENT_DISPLAY_NAMES = {
    AGENT_PORTFOLIO: "Portfolio Insights",
    AGENT_CRM: "Client Relationship",
    AGENT_ADMIN: "Administrative",
}

# Capability blurbs injected into orchestrator prompts so routing stays in sync with the registry.
AGENT_CAPABILITIES = {
    AGENT_PORTFOLIO: (
        "Portfolio data from the AAA system: summary, holdings, allocation, performance, risk and returns "
        "for a client's portfolio."
    ),
    AGENT_CRM: (
        "Client relationship data: emails and meetings (Outlook) and call transcripts (Fano), plus client "
        "concerns, sentiment, commitments and follow-ups."
    ),
    AGENT_ADMIN: (
        "Administrative knowledge: firm policies, processes, procedures, compliance rules and how-to guidance "
        "served by the knowledge base."
    ),
}

# Per-agent outcome status
AGENT_STATUS_OK = "ok"
AGENT_STATUS_PARTIAL = "partial"
AGENT_STATUS_NO_DATA = "no_data"
AGENT_STATUS_ERROR = "error"

# Why an agent loop ended (ReAct reasons live in app/components/react/models.py)
AGENT_TERMINATED_SINGLE_CALL = "single_call"
ADMIN_EXTERNAL_TOOL_NAME = "administrative_knowledge_agent"
# Tool text can point at one of its sources with this token; the output reducer swaps it for the [sourceN] marker.
SOURCE_REF_TOKEN = "[[ref:{source_id}]]"

# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
INTENT_GREETING = "greeting"
INTENT_OUT_OF_SCOPE = "out_of_scope"
INTENT_DATA_REQUEST = "data_request"
INTENT_NEEDS_CLARIFICATION = "needs_clarification"

EXEC_MODE_NONE = "none"
EXEC_MODE_SINGLE = "single"
EXEC_MODE_PARALLEL = "parallel"
EXEC_MODE_SEQUENTIAL = "sequential"

# Orchestrator phases; each phase selects its own prompt bundle.
PHASE_ROUTING = "routing"
PHASE_CLARIFICATION_FOLLOWUP = "clarification_followup"
PHASE_REPLAN = "replan"
PHASE_HUMAN_IN_THE_LOOP = "human_in_the_loop"

# Sufficiency verdicts
VERDICT_SUFFICIENT = "sufficient"
VERDICT_REPLAN = "replan"
VERDICT_ASK_HUMAN = "ask_human"
VERDICT_PARTIAL = "partial"

# ---------------------------------------------------------------------------
# Graph node names
# ---------------------------------------------------------------------------
NODE_ORCHESTRATOR = "orchestrator"
NODE_DIRECT_REPLY = "direct_reply"
NODE_SAFE_DECLINE = "safe_decline"
NODE_ASK_HUMAN = "ask_human"
NODE_STAGE_DISPATCH = "stage_dispatch"
NODE_PORTFOLIO_AGENT = "portfolio_agent"
NODE_CRM_AGENT = "crm_agent"
NODE_ADMIN_AGENT = "admin_agent"
NODE_STAGE_JOIN = "stage_join"
NODE_REDUCE_OUTPUTS = "reduce_outputs"
NODE_SUFFICIENCY = "sufficiency_check"
NODE_FINAL_AGENT = "final_agent"

AGENT_TO_NODE = {
    AGENT_PORTFOLIO: NODE_PORTFOLIO_AGENT,
    AGENT_CRM: NODE_CRM_AGENT,
    AGENT_ADMIN: NODE_ADMIN_AGENT,
}

# Nodes shown as steps in the Unique chat UI; other nodes stay internal.
STEP_LABELS = {
    NODE_ORCHESTRATOR: "Understanding your request",
    NODE_PORTFOLIO_AGENT: "Retrieving portfolio data",
    NODE_CRM_AGENT: "Retrieving client relationship data",
    NODE_ADMIN_AGENT: "Looking up policies and processes",
    NODE_SUFFICIENCY: "Checking the evidence",
    NODE_FINAL_AGENT: "Writing the answer",
}

# How a turn ended
OUTCOME_ANSWER = "answer"
OUTCOME_GREETING = "greeting"
OUTCOME_DECLINED = "declined"
OUTCOME_CLARIFICATION = "clarification"

GRAPH_RECURSION_LIMIT = 60

# ---------------------------------------------------------------------------
# Limits (env-tunable counterparts live in app/config.py)
# ---------------------------------------------------------------------------
HANDOFF_MAX_CHARS = 2500

LLM_PROVIDER_UNIQUE = "unique_ai"
LLM_PROVIDER_OPENAI = "openai"
VALID_LLM_PROVIDERS = frozenset({LLM_PROVIDER_UNIQUE, LLM_PROVIDER_OPENAI})

HISTORY_BLOCK_MAX_CONTENT_LEN = 20000

# How much of a payload (prompts, replies, tool results) log lines show; see LOG_PAYLOAD_MODE in app/config.py
LOG_PAYLOAD_MODE_SHORT = "short"
LOG_PAYLOAD_MODE_FULL = "full"

SSE_RESPONSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

CORRELATION_ID_HEADER = "X-Correlation-ID"
