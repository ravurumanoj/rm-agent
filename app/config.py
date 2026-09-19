import os
from pathlib import Path

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.constants import (
    LLM_PROVIDER_GEMINI,
    LLM_PROVIDER_OPENAI,
    LLM_PROVIDER_UNIQUE,
)


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
    DESCRIPTION: str = "Agentic AI FastAPI assistant for relationship managers."
    DEBUG: bool = False
    RELOAD: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "info"
    BACKEND_CORS_ORIGINS: list[str] = []

    LLM_PROVIDER: str = "openai"
    LLM_FALLBACK_MODELS: str = ""

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

    SSE_ENABLED: bool = False
    SSE_WEBHOOK_URL: str = "http://127.0.0.1:8000/relationship-manager/webhook"
    SSE_MAX_CONCURRENT: int = 10
    SUBSCRIPTIONS: list[str] = []

    LLM_MAX_RETRIES: int = 3
    LLM_RETRY_BASE_DELAY: float = 1.0
    LLM_RETRY_BACKOFF_MULTIPLIER: float = 2.0
    LLM_RETRY_MAX_DELAY: float = 30.0

    SSL_CA_CERT_PATH: str = ""
    SSL_VERIFY: bool = True
    HTTP_PROXY: str = ""
    HTTPS_PROXY: str = ""
    NO_PROXY: str = ""

    EMBEDDING_MODEL: str = "models/gemini-embedding-001"
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
    ENABLE_FILE_LOGGING: bool = True
    LOG_FILE: str = "logs/app.log"

    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    OPENAI_TEMPERATURE: float = 0.7
    OPENAI_MAX_TOKENS: int = 8192
    OPENAI_BASE_URL: str = ""

    GOOGLE_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-1.5-pro"
    GEMINI_TEMPERATURE: float = 0.7
    GEMINI_MAX_TOKENS: int = 8192

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

    def required_env_keys_for_provider(
        self,
        provider: Optional[str] = None,
    ) -> list[str]:
        """Return required environment keys for the selected LLM provider."""

        active = (provider or self.LLM_PROVIDER or "").lower().strip()
        if active == LLM_PROVIDER_OPENAI:
            return ["OPENAI_API_KEY"]
        if active == LLM_PROVIDER_GEMINI:
            return ["GOOGLE_API_KEY"]
        if active == LLM_PROVIDER_UNIQUE:
            return [
                "UNIQUE_API_BASE_URL",
                "UNIQUE_MODEL_NAME",
                "UNIQUE_APP_ID",
                "UNIQUE_APP_KEY",
                "UNIQUE_COMPANY_ID",
                "UNIQUE_USER_ID",
            ]
        return []

    def missing_runtime_settings(self) -> dict[str, list[str]]:
        """Return missing runtime keys grouped by concern.

        This is intentionally non-throwing so the app can boot in
        scaffold mode.
        """
        missing_provider: list[str] = []
        active = (self.LLM_PROVIDER or "").lower().strip()
        if active == LLM_PROVIDER_UNIQUE:
            base_keys = [
                "UNIQUE_API_BASE_URL",
                "UNIQUE_MODEL_NAME",
                "UNIQUE_APP_ID",
                "UNIQUE_APP_KEY",
            ]
            missing_provider.extend(
                [
                    key
                    for key in base_keys
                    if not str(getattr(self, key, "")).strip()
                ]
            )
            if not self.UNIQUE_COMPANY_ID.strip():
                missing_provider.append("UNIQUE_COMPANY_ID")
            if not self.UNIQUE_USER_ID.strip():
                missing_provider.append("UNIQUE_USER_ID")
        else:
            missing_provider.extend(
                [
                    key
                    for key in self.required_env_keys_for_provider()
                    if not str(getattr(self, key, "")).strip()
                ]
            )

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