from pydantic import BaseModel, Field


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


class UniqueModelItem(BaseModel):
    model: str
    provider: str = "unique_ai"
    raw: dict = Field(default_factory=dict)


class UniqueModelListResponse(BaseModel):
    models: list[UniqueModelItem] = Field(default_factory=list)


class UniqueModelTestRequest(BaseModel):
    model: str = Field(..., min_length=1, description="Unique model name to test.")
    query: str = Field(..., min_length=1, description="Prompt/query to send to the model.")
    company_id: str = Field(default="", description="Optional runtime override for tenant identity.")
    user_id: str = Field(default="", description="Optional runtime override for user identity.")


class UniqueModelTestResponse(BaseModel):
    model: str
    query: str
    response: str


class ChatResponse(BaseModel):
    """Final agent reply for a turn."""

    session_id: str
    reply: str
    citations: list[CitationItem] = Field(default_factory=list)
    evaluations: list[EvaluationItem] = Field(default_factory=list)


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

    chatId: str = ""
    assistantId: str = ""
    text: str = ""
    userMessage: WebhookUserMessage = Field(default_factory=WebhookUserMessage)
    assistantMessage: WebhookAssistantMessage = Field(default_factory=WebhookAssistantMessage)
    configuration: dict = Field(default_factory=dict)


class WebhookEvent(BaseModel):
    model_config = {"extra": "allow"}

    id: str = ""
    version: str = ""
    event: str = ""
    createdAt: int | str | None = None
    userId: str = ""
    companyId: str = ""
    payload: WebhookPayload = Field(default_factory=WebhookPayload)
