## rm-agent

Agentic FastAPI backend for Relationship Managers.

Primary goals:
- Answer RM questions about portfolios and client interactions.
- Support transcription/meeting summarization using retrieved CRM/portfolio context.
- Keep responses grounded in tool data with citations/evaluations.

---

## 1. Run the Application 

### A) Local dev run (recommended)

```powershell
uv run main.py
```


### B) Run with explicit server parameters

```powershell
python main.py --host 0.0.0.0 --port 8000 --reload --log-level debug
```

### C) Run with a specific env file

```powershell
python main.py --env-file .env.local --port 8010
```

### D) Uvicorn direct mode

```powershell
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### E) Docker run

```powershell
docker build -t rm-agent .
docker run --rm -p 8000:8000 --env-file .env rm-agent
```

---

## 2. Command Parameters and Purpose

`main.py` supports:
- `--env-file`: load settings from a specific env file.
- `--host`: bind host interface.
- `--port`: bind port.
- `--log-level`: uvicorn level (`critical|error|warning|info|debug|trace`).
- `--debug`: force `DEBUG=true` for this run.
- `--reload`: force reload on.
- `--no-reload`: force reload off.

---

## 3. Switchable Concepts (Enable/Disable)

### LLM provider selection
- `LLM_PROVIDER=openai|gemini|unique_ai`
- Provider-specific required settings are validated at startup.

### Postgres on/off
- `POSTGRES_ENABLED=true|false`

Behavior:
- `true`: DB engine/session available; audit persistence uses DB.
- `false`: DB init/session CRUD are skipped safely; `/health` shows `postgresql=disabled`.

### DB startup strictness
- `DB_STARTUP_REQUIRED=true|false`

Behavior:
- `true`: app fails startup if DB init fails.
- `false`: app starts in degraded mode if DB is unreachable.

### Phoenix observability mode
- `PHOENIX_ENABLED=true|false`
- `PHOENIX_LOCAL_MODE=true|false`

Local mode:
- `PHOENIX_OTLP_ENDPOINT=http://127.0.0.1:6006/v1/traces`

Deployed mode:
- `PHOENIX_DEPLOYED_OTLP_ENDPOINT=...`
- `PHOENIX_SPACE_ID=...`
- `PHOENIX_API_KEY=...`
- Optional header name overrides:
	- `PHOENIX_SPACE_ID_HEADER`
	- `PHOENIX_API_KEY_HEADER`

### Unique webhook signature verification
- `UNIQUE_WEBHOOK_VERIFY_SIGNATURE=true|false`
- `UNIQUE_WEBHOOK_ENDPOINT_SECRET=...`
- `UNIQUE_WEBHOOK_EXPECTED_MODULE_NAME=...` (optional safety filter)

Behavior:
- `false` (default): signature verification is skipped for local testing.
- `true`: signature verification is enforced using `UNIQUE_WEBHOOK_ENDPOINT_SECRET`.
- If verification is enabled but secret is empty, webhook endpoint returns `503 verification_unavailable`.
- If `UNIQUE_WEBHOOK_EXPECTED_MODULE_NAME` is set, external-module events with different module names are ignored.

### Feature toggles
- `MCP_ENABLED`
- `GRAPH_CHECKPOINTER_ENABLED`
- `AUDIT_ENABLED`
- `ENTITLEMENTS_ENABLED`
- `GUARDRAILS_ENABLED`
- `PII_MASKING_ENABLED`
- `LTM_ENABLED`

---

## 4. End-to-End Request Flow (Simple)

1. API receives RM query (`/agent/chat` or `/agent/stream`) or Unique webhook event.
2. Middleware attaches/propagates correlation ID (`X-Correlation-ID`).
3. Orchestrator routes request:
	 - greeting / out_of_scope / portfolio_only / crm_only / both
4. Portfolio context resolver extracts portfolio IDs from user question.
5. Clarification gate asks back if required details are missing/ambiguous.
6. Sub-agents execute tools:
	 - Portfolio Insights sub-agent
	 - Relationship Intelligence sub-agent
7. Multi-portfolio behavior:
	 - If multiple IDs are selected, sub-agents fan out calls per portfolio ID.
8. Sufficiency check + optional retry/replan for missing data.
9. Synthesizer produces grounded final answer.
10. Citations + evaluations are returned.

If required data is still unavailable after retries, final response explicitly states what section is unavailable.

---

## 5. What We Use and Why

### FastAPI
- REST and SSE endpoints.
- Health, stream, webhook, model utility APIs.

### LangGraph concepts
- Shared `AgentState` orchestration pattern (`router -> clarification -> execute -> synthesize -> evaluate`).
- Checkpoint-like conversational continuity through in-memory checkpointer.

### Unique AI SDK (direct usage)
Used in these backend capabilities:
- `unique_sdk.ChatCompletion.create`: model invocation path in Unique provider wrapper.
- `unique_sdk.LLMModels.*`: list available Unique models.
- `unique_sdk.Webhook.construct_event`: webhook signature verification (when enabled).
- `unique_sdk.Message.modify/create`: write assistant response back to Unique chat.

### Unique Toolkit
- Not directly imported as runtime code path in this backend currently.
- MCP/tooling architecture is designed to integrate external tool ecosystems cleanly.

### Arize Phoenix + OpenTelemetry
- LLM tracing and metadata observability.
- Local or deployed collector modes supported.

---

## 6. Key API Endpoints

- `POST /agent/chat`: single-turn response.
- `POST /agent/stream`: SSE streamed response.
- `GET /health`: liveness/dependency health.
- `POST /relationship-manager/webhook`: Unique-compatible webhook.
- `GET /relationship-manager/webhook/events`: webhook lifecycle SSE stream.
- `GET /relationship-manager/webhook/events/recent`: recent webhook events.
- `GET /agent/unique/models`: list Unique models.
- `POST /agent/unique/models/test`: test a specific Unique model.

---

## 7. Correlation ID Tracing

Behavior:
- Backend generates a fresh UUID correlation ID for every incoming request.
- Response always includes `X-Correlation-ID`.
- Logs include `correlation_id=...` for end-to-end tracing.

---

## 8. Multi-Portfolio Dynamic Handling

What is implemented:
- Portfolio IDs are extracted from RM query text.
- IDs are validated against active portfolio scope when provided.
- If query asks for multiple portfolio IDs, sub-agents perform per-ID calls.
- If any requested ID is unmatched, assistant asks clarification instead of silently dropping IDs.

Expected metadata for best behavior:
- `active_portfolios`: list of portfolio objects with `id`.
- `client_id`: selected customer/client ID.

Example request:

```json
{
	"session_id": "s-001",
	"message": "Compare portfolio IDs PF-1001 and PF-2002 for last quarter",
	"metadata": {
		"client_id": "CUST001",
		"active_portfolios": [
			{"id": "PF-1001"},
			{"id": "PF-2002"}
		]
	}
}
```

---

## 9. Setup Notes

Install (development):

```powershell
pip install -e ".[dev]"
```

Optional extras:

```powershell
pip install -e ".[unique,openai,gemini,phoenix,dev]"
```

Phoenix local server:

```powershell
phoenix serve
```

---

## 10. Troubleshooting Quick Checks

1. If `uv run ...` fails with packaging/module errors:
- Confirm `src/rm_agent/__init__.py` exists (entrypoint module).

2. If `uv run ...` shows Access is denied while removing dist-info:
- This can happen in OneDrive-backed folders due file locks.
- Retry after closing active Python processes, or run directly with `.venv\\Scripts\\python.exe main.py`.

3. If webhook signature errors occur:
- For local webhook testing, set `UNIQUE_WEBHOOK_VERIFY_SIGNATURE=false`.
- For strict verification, set `UNIQUE_WEBHOOK_VERIFY_SIGNATURE=true` and provide `UNIQUE_WEBHOOK_ENDPOINT_SECRET`.

4. If no DB operations should run:
- Set `POSTGRES_ENABLED=false`.

5. If traces are not visible:
- Check `PHOENIX_ENABLED`, endpoint URL, and mode (`PHOENIX_LOCAL_MODE`).

---

## 11. Local Webhook Intercept and SSE Listener Flow

You already have built-in webhook event streaming endpoints:
- `GET /relationship-manager/webhook/events`
- `GET /relationship-manager/webhook/events/recent`

A helper script is available for local inspection:
- `scripts/webhook_sse_tap.py`

Run steps:

1. Start backend:

```powershell
uv run main.py
```

2. Keep webhook verification optional for local UI testing:

```env
UNIQUE_WEBHOOK_VERIFY_SIGNATURE=false
```

3. Open live webhook lifecycle stream:

```powershell
python scripts/webhook_sse_tap.py --base-url http://127.0.0.1:8000
```

4. Optional: filter by one chat id:

```powershell
python scripts/webhook_sse_tap.py --chat-id <chat_id>
```

5. Optional: get recent snapshot instead of live stream:

```powershell
python scripts/webhook_sse_tap.py --recent --limit 100
```

Expected events include:
- `webhook_received`
- `webhook_signature` (`mode=enforced` or `mode=skipped`)
- `webhook_parsed`
- `orchestrator_started`
- `orchestrator_completed`
- `webhook_handled`
- failure paths such as `webhook_rejected` or `webhook_writeback_failed`

---

## 12. Unique Event Socket Drop-In (No ngrok)

Based on Unique Event Socket docs, this backend supports a local drop-in flow:
1. Subscribe to Unique Event Socket (SSE) from your machine.
2. Forward each received event to your local webhook endpoint.

Script provided:
- `scripts/unique_event_socket_dropin.py`

Why this matters:
- You can test Unique UI -> local backend webhook flow without exposing local port publicly.
- Event Socket is development-only and the connection can close after about 10 minutes; script reconnects.

Before running:
- Keep `UNIQUE_WEBHOOK_VERIFY_SIGNATURE=false` for drop-in mode.
- Set Unique credentials in `.env`: `UNIQUE_APP_ID`, `UNIQUE_APP_KEY`, `UNIQUE_COMPANY_ID`, `UNIQUE_API_BASE_URL`.
- Optional strict module filter: `UNIQUE_WEBHOOK_EXPECTED_MODULE_NAME=<your module name in Unique UI>`.

Run backend:

```powershell
uv run main.py
```

Run drop-in forwarder:

```powershell
python scripts/unique_event_socket_dropin.py --subscriptions unique.chat.external-module.chosen
```

Common options:

```powershell
# Explicit local webhook URL
python scripts/unique_event_socket_dropin.py --local-webhook-url http://127.0.0.1:8000/relationship-manager/webhook

# Override full stream URL directly
python scripts/unique_event_socket_dropin.py --stream-url https://gateway.<tenant>.unique.app/public/event-socket/events/stream --subscriptions unique.chat.external-module.chosen

# Stop after N events for quick checks
python scripts/unique_event_socket_dropin.py --max-events 3
```

Doc-aligned event handling:
- Official names are accepted: `unique.chat.external-module.chosen`, `unique.chat.user-message.created`.
- Legacy short names are also accepted for compatibility.

---

## 13. Create Project Structure Script (PowerShell)

Script location:
- `scripts/create_project_structure.ps1`

Run from project root:

```powershell
Set-Location "C:\Users\manoj.ravuru\OneDrive - Accenture\Desktop\fastapi_projects\rm-agent"
powershell -ExecutionPolicy Bypass -File .\scripts\create_project_structure.ps1 -ProjectRoot "my-new-project"
```

Optional overwrite mode:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\create_project_structure.ps1 -ProjectRoot "my-new-project" -Force
```

Run from scripts folder:

```powershell
Set-Location "C:\Users\manoj.ravuru\OneDrive - Accenture\Desktop\fastapi_projects\rm-agent\scripts"
powershell -ExecutionPolicy Bypass -File .\create_project_structure.ps1 -ProjectRoot "my-new-project"
```

If script execution is blocked in PowerShell, run once in that terminal:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

