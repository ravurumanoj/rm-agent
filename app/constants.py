# Route/response constants shared across the app.

DOCS_URL = "/docs"
REDOC_URL = "/redoc"
OPENAPI_URL = "/openapi.json"

HEALTH_STATUS_HEALTHY = "healthy"
HEALTH_STATUS_DEGRADED = "degraded"

# LangGraph routing labels (app/agents/router.py picks one of these per turn).
ROUTE_GREETING = "greeting"
ROUTE_OUT_OF_SCOPE = "out_of_scope"
ROUTE_GENERAL = "general"
ROUTE_AGENT = "agent"

# Expanded route labels used by richer router/execution logic in the source app.
ROUTE_PORTFOLIO_ONLY = "portfolio_only"
ROUTE_CRM_ONLY = "crm_only"
ROUTE_BOTH = "both"

VALID_ROUTES = frozenset(
    {
        ROUTE_GREETING,
        ROUTE_OUT_OF_SCOPE,
        ROUTE_GENERAL,
        ROUTE_AGENT,
        ROUTE_PORTFOLIO_ONLY,
        ROUTE_CRM_ONLY,
        ROUTE_BOTH,
    }
)

DATA_ROUTES = frozenset({ROUTE_PORTFOLIO_ONLY, ROUTE_CRM_ONLY, ROUTE_BOTH})

EXEC_MODE_PARALLEL = "parallel"
EXEC_MODE_SEQUENTIAL = "sequential"
VALID_EXEC_MODES = frozenset({EXEC_MODE_PARALLEL, EXEC_MODE_SEQUENTIAL})

AGENT_PORTFOLIO = "portfolio"
AGENT_CRM = "crm"
VALID_AGENTS = frozenset({AGENT_PORTFOLIO, AGENT_CRM})

SEQUENTIAL_HANDOFF_MAX_LEN = 1500

LLM_PROVIDER_UNIQUE = "unique_ai"

DEFAULT_LLM_MAX_RETRIES = 3
DEFAULT_LLM_RETRY_BASE_DELAY = 1.0
DEFAULT_LLM_RETRY_BACKOFF_MULTIPLIER = 2.0
DEFAULT_LLM_RETRY_MAX_DELAY = 30.0

LLM_PROVIDER_OPENAI = "openai"
LLM_PROVIDER_GEMINI = "gemini"

OPENAI_MODEL_GPT4O = "gpt-4o"
OPENAI_MODEL_GPT4O_MINI = "gpt-4o-mini"
OPENAI_MODEL_GPT4_TURBO = "gpt-4-turbo"
OPENAI_MODEL_GPT35_TURBO = "gpt-3.5-turbo"

GEMINI_MODEL_2_FLASH = "gemini-2.0-flash"
GEMINI_MODEL_2_PRO = "gemini-2.0-pro-exp"
GEMINI_MODEL_15_PRO = "gemini-1.5-pro"
GEMINI_MODEL_15_FLASH = "gemini-1.5-flash"

MODEL_TO_PROVIDER = {
    GEMINI_MODEL_2_FLASH: LLM_PROVIDER_GEMINI,
    GEMINI_MODEL_2_PRO: LLM_PROVIDER_GEMINI,
    GEMINI_MODEL_15_PRO: LLM_PROVIDER_GEMINI,
    GEMINI_MODEL_15_FLASH: LLM_PROVIDER_GEMINI,
    OPENAI_MODEL_GPT4O: LLM_PROVIDER_OPENAI,
    OPENAI_MODEL_GPT4O_MINI: LLM_PROVIDER_OPENAI,
    OPENAI_MODEL_GPT4_TURBO: LLM_PROVIDER_OPENAI,
    OPENAI_MODEL_GPT35_TURBO: LLM_PROVIDER_OPENAI,
}

MAX_RETRIES = 2

HISTORY_BLOCK_MAX_CONTENT_LEN = 200

SAFE_DECLINE_MESSAGE = (
    "I'm the Relationship Manager assistant, so I can only help with client "
    "portfolio insights and client relationship/meeting information. I can't help "
    "with that request, but feel free to ask me about a client's portfolio, "
    "performance, holdings, meetings, or follow-up actions."
)

SSE_RESPONSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

CORRELATION_ID_HEADER = "X-Correlation-ID"

CLARIFY_PROCEED = "proceed"
CLARIFY_ASK = "clarify"
VALID_CLARIFY_ACTIONS = frozenset({CLARIFY_PROCEED, CLARIFY_ASK})
