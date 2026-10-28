from typing import Any

from pydantic import BaseModel, Field, model_validator


class ChatRequest(BaseModel):
    """Incoming request for a single agent turn."""

    session_id: str = Field(..., description="Conversation/thread identifier for checkpointing.")
    message: str = Field(..., min_length=1, description="User's natural-language message.")
    metadata: dict = Field(default_factory=dict, description="Optional client/context metadata for routing and tools.")
    tool_outputs: list[dict] = Field(default_factory=list, description="Optional pre-fetched tool outputs for deterministic orchestration.")


class CitationItem(BaseModel):
    source_number: int
    marker: str
    content_id: str
    title: str = ""
    snippet: str = ""
    uri: str = ""
    metadata: dict = Field(default_factory=dict)


class EvaluationItem(BaseModel):
    name: str
    passed: bool
    score: float
    severity: str
    summary: str
    details: dict = Field(default_factory=dict)


class ChatResponse(BaseModel):
    """Final agent reply for a turn."""

    session_id: str
    reply: str
    citations: list[CitationItem] = Field(default_factory=list)
    evaluations: list[EvaluationItem] = Field(default_factory=list)


class ProviderModel(BaseModel):
    model: str = Field(..., description="Model name.")
    configured: bool = Field(..., description="True when set in this app's config (model, model list or fallbacks).")


class ProviderModels(BaseModel):
    provider: str = Field(..., description="unique_ai or openai (LLMaaS).")
    source: str = Field(..., description="unique_platform: live list from Unique; config: this app's config.")
    models: list[ProviderModel] = Field(default_factory=list, description="Configured models first.")
    error: str | None = Field(default=None, description="Why the live list could not be read, if it failed.")


class ModelsResponse(BaseModel):
    default_provider: str = Field(..., description="LLM_PROVIDER: the provider the agents use.")
    default_model: str = Field(..., description="The agents' primary model.")
    providers: list[ProviderModels] = Field(default_factory=list)


class LLMQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Prompt to send to the model.")
    model: str | None = Field(default=None, description="Model to test. Defaults to the provider's configured model.")
    provider: str | None = Field(
        default=None, description="unique_ai or openai. Defaults to the provider that lists the model in config."
    )


class LLMQueryResponse(BaseModel):
    response: str = Field(..., description="The model's reply.")
    model: str = Field(..., description="Model that answered.")
    provider: str = Field(..., description="Provider that served the call.")
    latency_ms: int = Field(..., description="Round-trip time of the call.")


class WebhookUserMessage(BaseModel):
    id: str = ""
    text: str = ""
    createdAt: str | None = None
    originalText: str | None = None
    language: str | None = None


class WebhookAssistantMessage(BaseModel):
    id: str = ""
    createdAt: str | None = None


class WebhookPayload(BaseModel):
    model_config = {"extra": "allow"}

    name: str = ""
    description: str = ""
    chatId: str = ""
    assistantId: str = ""
    text: str = ""
    userMessage: WebhookUserMessage = Field(default_factory=WebhookUserMessage)
    assistantMessage: WebhookAssistantMessage = Field(default_factory=WebhookAssistantMessage)
    configuration: dict = Field(default_factory=dict)
    # Model the user picked for this turn: a model name, or a model-info object with "name" (unique_toolkit ChatEventPayload).
    modelChoice: Any = None

    @model_validator(mode="before")
    @classmethod
    def _accept_lowercase_chat_id(cls, data: Any) -> Any:
        # The external-module docs spell the key "chatid"; the user-message event uses "chatId".
        if isinstance(data, dict) and not data.get("chatId") and data.get("chatid"):
            return {**data, "chatId": data["chatid"]}
        return data


class WebhookEvent(BaseModel):
    model_config = {"extra": "allow"}

    id: str = ""
    version: str = ""
    event: str = ""
    createdAt: int | str | None = None
    userId: str = ""
    companyId: str = ""
    payload: WebhookPayload = Field(default_factory=WebhookPayload)
