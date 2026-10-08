import os
from pathlib import Path
from typing import ClassVar, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.constants import LLM_PROVIDER_OPENAI, LLM_PROVIDER_UNIQUE, LOG_PAYLOAD_MODE_FULL, LOG_PAYLOAD_MODE_SHORT


_DEFAULT_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
ENV_FILE = os.getenv("APP_ENV_FILE", str(_DEFAULT_ENV_FILE))


class Settings(BaseSettings):
    """Environment-driven application settings. See .env.example for all
    keys."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    APP_NAME: str = "rm-agent"
    API_V1_STR: str = ""
    VERSION: str = "0.1.0"
    DESCRIPTION: str = "Agentic AI assistant for relationship managers."
    DEBUG: bool = False
    RELOAD: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "info"
    BACKEND_CORS_ORIGINS: list[str] = []

    LLM_PROVIDER: str = LLM_PROVIDER_UNIQUE
    LLM_FALLBACK_MODELS: str = ""

    # LLM-as-a-service (LLMaaS) endpoint exposing the OpenAI API; the client provides the key.
    LLMAAS_BASE_URL: str = ""
    LLMAAS_API_KEY: str = ""
    LLMAAS_MODEL: str = ""
    LLMAAS_TEMPERATURE: float = 0.7
    LLMAAS_MAX_TOKENS: int = 8192

    UNIQUE_API_BASE_URL: str = ""
    UNIQUE_API_VERSION: str = "2023-12-06"
    UNIQUE_MODEL_NAME: str = ""
    UNIQUE_APP_ID: str = ""
    UNIQUE_APP_KEY: str = ""
    UNIQUE_COMPANY_ID: str = "307071782207144087"
    UNIQUE_USER_ID: str = "380715897637093530"
    UNIQUE_WEBHOOK_VERIFY_SIGNATURE: bool = False
    UNIQUE_WEBHOOK_ENDPOINT_SECRET: str = ""
    UNIQUE_WEBHOOK_EXPECTED_MODULE_NAME: str = ""
    UNIQUE_STEPS_ENABLED: bool = True
    UNIQUE_STREAM_REPLY: bool = False
    UNIQUE_STREAM_CHUNK_WORDS: int = 20

    SSE_ENABLED: bool = False
    SSE_WEBHOOK_URL: str = "http://127.0.0.1:8000/relationship-manager/webhook"
    SSE_MAX_CONCURRENT: int = 10
    SSE_URL: str = ""
    SSE_SUBSCRIPTIONS: str = "unique.chat.external-module.chosen"  # comma-separated
    SSE_ASSISTANT_ID: str = ""  # blank: accept events for any assistant

    LLM_MAX_RETRIES: int = 3
    LLM_RETRY_BASE_DELAY: float = 1.0
    LLM_RETRY_BACKOFF_MULTIPLIER: float = 2.0
    LLM_RETRY_MAX_DELAY: float = 30.0

    SSL_CA_CERT_PATH: str = ""
    SSL_VERIFY: bool = True
    HTTP_PROXY: str = ""
    HTTPS_PROXY: str = ""
    NO_PROXY: str = ""

    MEMORY_STORAGE_PATH: str = "data/memory/sessions.json"
    LONG_TERM_MEMORY_PATH: str = "data/memory/long_term_memory.json"

    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DATABASE: str = "rm_agent"
    POSTGRES_ENABLED: bool = True
    MEMORY_DATABASE_URL_OVERRIDE: Optional[str] = None
    DB_ECHO: bool = False
    DB_STARTUP_REQUIRED: bool = False
    DB_CONNECT_TIMEOUT_SECONDS: int = 3

    MCP_ENABLED: bool = False
    MCP_SERVER_URL: str = ""
    MCP_CRM_SERVER_URL: str = ""
    MCP_TRANSPORT: str = "streamable_http"
    MCP_TIMEOUT: float = 15.0
    MCP_MAX_TOOL_ITERATIONS: int = 5

    GRAPH_CHECKPOINTER_ENABLED: bool = True

    ENTITLEMENTS_ENABLED: bool = True
    AUDIT_ENABLED: bool = True
    GUARDRAILS_ENABLED: bool = True
    PII_MASKING_ENABLED: bool = False

    PHOENIX_ENABLED: bool = False
    PHOENIX_PROJECT_NAME: str = "rm-agent"
    PHOENIX_OTLP_ENDPOINT: str = "http://127.0.0.1:6006/v1/traces"
    PHOENIX_LOCAL_MODE: bool = True
    PHOENIX_DEPLOYED_OTLP_ENDPOINT: str = ""
    PHOENIX_SPACE_ID: str = ""
    PHOENIX_API_KEY: str = ""
    PHOENIX_CAPTURE_MESSAGE_CONTENT: bool = False

    LTM_ENABLED: bool = True
    QDRANT_LOCATION: str = ":memory:"
    QDRANT_EPISODIC_COLLECTION: str = "episodic_memory"
    QDRANT_EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    EPISODIC_TOP_K: int = 3
    EPISODIC_MIN_SCORE: float = 0.30
    EPISODIC_SUMMARY_MAX_LEN: int = 400

    DEFAULT_AGENT_TIMEOUT: int = 60
    MAX_AGENT_ITERATIONS: int = 5
    AGENT_MAX_REPLAN_LOOPS: int = 2
    AGENT_TOOL_TIMEOUT_SECONDS: float = 30.0
    AGENT_MAX_PARALLEL_TOOL_CALLS: int = 8
    AGENT_EVIDENCE_MAX_CHARS_PER_SOURCE: int = 4000
    AGENT_STRUCTURED_OUTPUT_ATTEMPTS: int = 2
    ADMIN_AGENT_TIMEOUT_SECONDS: float = 60.0
    ADMIN_CLM_ASSISTANT_ID: str = "assistant_bpo9dt6zgqei@gv2ay1oozu3"
    ADMIN_CLM_CHAT_ID: str = "chat_zz6lz1je6v4z3pjyaa8i7fz2"
    ENABLE_FILE_LOGGING: bool = True
    LOG_FILE: str = "logs/app.log"
    # "short" logs only the first/last LOG_PAYLOAD_EDGE_CHARS chars of prompts, replies and tool results; "full" logs all.
    LOG_PAYLOAD_MODE: str = LOG_PAYLOAD_MODE_SHORT
    LOG_PAYLOAD_EDGE_CHARS: int = 150

    @property
    def LOG_PAYLOAD_FULL(self) -> bool:
        return self.LOG_PAYLOAD_MODE.strip().lower() == LOG_PAYLOAD_MODE_FULL

    @property
    def LLM_FALLBACK_MODELS_LIST(self) -> list:
        """Return fallback model names from LLM_FALLBACK_MODELS."""
        if not self.LLM_FALLBACK_MODELS:
            return []
        return [
            m.strip()
            for m in self.LLM_FALLBACK_MODELS.split(",")
            if m.strip()
        ]

    @property
    def MEMORY_DATABASE_URL(self) -> str:
        """Return SQLAlchemy DB URL used by memory/checkpointer
        components."""
        if self.MEMORY_DATABASE_URL_OVERRIDE:
            return self.MEMORY_DATABASE_URL_OVERRIDE
        from urllib.parse import quote_plus

        password = quote_plus(self.POSTGRES_PASSWORD)
        return (
            f"postgresql+psycopg://{self.POSTGRES_USER}:{password}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DATABASE}"
        )

    @property
    def MCP_EFFECTIVE_SERVER_URL(self) -> str:
        """Compatibility alias for legacy/new MCP URL setting names."""
        return self.MCP_CRM_SERVER_URL or self.MCP_SERVER_URL

    @property
    def PHOENIX_EFFECTIVE_OTLP_ENDPOINT(self) -> str:
        """Resolve the OTLP endpoint based on local/deployed mode."""
        if self.PHOENIX_LOCAL_MODE:
            return self.PHOENIX_OTLP_ENDPOINT
        return (
            self.PHOENIX_DEPLOYED_OTLP_ENDPOINT
            or self.PHOENIX_OTLP_ENDPOINT
        )

    @property
    def PHOENIX_EFFECTIVE_OTLP_HEADERS(self) -> dict[str, str]:
        """Resolve OTLP headers for Phoenix exporter based on mode."""
        if self.PHOENIX_LOCAL_MODE:
            return {}

        headers: dict[str, str] = {}
        if self.PHOENIX_SPACE_ID.strip():
            headers["space_id"] = self.PHOENIX_SPACE_ID.strip()
        if self.PHOENIX_API_KEY.strip():
            headers["api_key"] = self.PHOENIX_API_KEY.strip()
        return headers

    REQUIRED_UNIQUE_KEYS: ClassVar[tuple[str, ...]] = (
        "UNIQUE_API_BASE_URL",
        "UNIQUE_MODEL_NAME",
        "UNIQUE_APP_ID",
        "UNIQUE_APP_KEY",
        "UNIQUE_COMPANY_ID",
        "UNIQUE_USER_ID",
    )

    def default_model_for(self, provider: Optional[str] = None) -> str:
        """Configured model name of the given (default: active) LLM provider."""
        active = (provider or self.LLM_PROVIDER).strip().lower()
        return (self.LLMAAS_MODEL if active == LLM_PROVIDER_OPENAI else self.UNIQUE_MODEL_NAME).strip()

    def missing_llm_settings(self) -> list[str]:
        """Required settings of the active LLM provider that are not set."""
        if self.LLM_PROVIDER.strip().lower() == LLM_PROVIDER_OPENAI:
            return [
                key
                for key in ("LLMAAS_BASE_URL", "LLMAAS_API_KEY", "LLMAAS_MODEL")
                if not str(getattr(self, key, "")).strip()
            ]
        return [key for key in self.REQUIRED_UNIQUE_KEYS if not str(getattr(self, key, "")).strip()]

    def missing_runtime_settings(self) -> dict[str, list[str]]:
        """Return missing runtime keys grouped by concern.

        This is intentionally non-throwing so the app can boot in
        scaffold mode.
        """
        missing_provider = self.missing_llm_settings()
        missing_mcp: list[str] = []
        if self.MCP_ENABLED and not self.MCP_EFFECTIVE_SERVER_URL.strip():
            missing_mcp.append(
                "MCP_SERVER_URL (or MCP_CRM_SERVER_URL)"
            )

        missing_phoenix: list[str] = []
        if self.PHOENIX_ENABLED:
            if not self.PHOENIX_EFFECTIVE_OTLP_ENDPOINT.strip():
                if self.PHOENIX_LOCAL_MODE:
                    missing_phoenix.append("PHOENIX_OTLP_ENDPOINT")
                else:
                    missing_phoenix.append(
                        "PHOENIX_DEPLOYED_OTLP_ENDPOINT "
                        "(or PHOENIX_OTLP_ENDPOINT)"
                    )

            if not self.PHOENIX_LOCAL_MODE:
                if not self.PHOENIX_SPACE_ID.strip():
                    missing_phoenix.append("PHOENIX_SPACE_ID")
                if not self.PHOENIX_API_KEY.strip():
                    missing_phoenix.append("PHOENIX_API_KEY")

        return {
            "provider": missing_provider,
            "mcp": missing_mcp,
            "phoenix": missing_phoenix,
        }


settings = Settings()