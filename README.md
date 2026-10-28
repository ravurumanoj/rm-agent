# rm-agent

Agentic FastAPI backend for Relationship Managers (RMs). It answers questions about portfolios, client interactions
and administrative topics, grounded in tool data with citations. It is used through its own REST API or as a Unique
AI external module (webhook).

## Table of contents

1. [Installation](#1-installation)
2. [Configuration](#2-configuration)
3. [Running the app](#3-running-the-app)
4. [Using the API](#4-using-the-api)
5. [SSE listener](#5-sse-listener)
6. [More documentation](#6-more-documentation)

---

## 1. Installation

Requirements: Python 3.14. Run every command from the project root.

```powershell
# 1. Install uv (the package manager used by this project)
pip install uv

# 2. Log in to the package repository, so uv can download the dependencies
uv auth login repo.artifactory-dotgen.group.echonet

# 3. Create the virtual environment (.venv) and install all dependencies, including the Unique SDK
uv sync --extra unique
```

To use an OpenAI-compatible LLM service instead of Unique, add `--extra openai` to the `uv sync` command.

---

## 2. Configuration

The app reads its settings from a `.env` file in the project root. Create it by copying `.env.example`, then fill
in your credentials and details:

```powershell
Copy-Item .env.example .env
```

Fill in at least these values in `.env`:

| Setting | What to enter |
|---|---|
| `UNIQUE_API_BASE_URL` | Unique API base URL, e.g. `https://gateway.<tenant>.unique.app/public/chat` |
| `UNIQUE_APP_ID`, `UNIQUE_APP_KEY` | Your Unique app credentials |
| `UNIQUE_COMPANY_ID`, `UNIQUE_USER_ID` | Your Unique company and user ids |
| `UNIQUE_MODEL_NAME` | LLM to use, e.g. `AZURE_GPT_4o_2024_1120` |
| `PHOENIX_SPACE_ID`, `PHOENIX_API_KEY`, `PHOENIX_DEPLOYED_OTLP_ENDPOINT` | Arize AX tracing credentials (with `PHOENIX_LOCAL_MODE=false`) |
| `HTTP_PROXY`, `HTTPS_PROXY`, `SSL_CA_CERT_PATH` | Only if your network needs a proxy or a company CA certificate |

Every setting is described in [PROJECT_GUIDE.md](PROJECT_GUIDE.md) (section 5).

---

## 3. Running the app

Without arguments, the app uses the `.env` file in the project root and the host and port set there (default
`0.0.0.0:8000`):

```powershell
uv run main.py
```

Check that it is up:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

With arguments, you can choose the host, the port and the env file for this run:

```powershell
# Another host and port
uv run main.py --host 127.0.0.1 --port 8010

# Another env file
uv run main.py --env-file .env.local

# All together
uv run main.py --env-file .env.local --host 0.0.0.0 --port 8010
```

| Argument | What it does |
|---|---|
| `--host HOST` | Interface to listen on: `0.0.0.0` (reachable from other machines) or `127.0.0.1` (this machine only) |
| `--port PORT` | Port to listen on |
| `--env-file PATH` | Env file to load instead of `.env` (relative to the current folder, or an absolute path) |
| `--log-level LEVEL` | `critical`, `error`, `warning`, `info`, `debug` or `trace` |
| `--reload` | Restart automatically when the code changes |

Arguments override the values in the env file. Arguments you leave out keep the env file's values.

---

## 4. Using the API

With the app running, the interactive API docs are at http://127.0.0.1:8000/docs. You can try every endpoint
there.

| Method and path | Purpose |
|---|---|
| `GET /health` | Checks that the app is running |
| `POST /agent/chat` | Sends one RM message to the agent and returns the reply with its citations |
| `POST /agent/stream` | Same as `/agent/chat`, streamed as Server-Sent Events |
| `POST /relationship-manager/webhook` | Webhook that Unique calls for chat events |
| `GET /agent/llm/models` | Lists the models per provider: `unique_ai` from the Unique platform, `openai` (LLMaaS) from config; configured models are flagged |
| `POST /agent/llm/query` | Tests one model: `{"query": "...", "model": "optional", "provider": "optional"}`; omitted values come from config |

Example: send a message to the agent.

```powershell
$body = '{"session_id": "s-001", "message": "How is portfolio PF-1001 performing this quarter?", "metadata": {"client_id": "CUST001"}}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/agent/chat -ContentType "application/json" -Body $body
```

`session_id` groups the messages of one conversation. Reuse it for follow-up questions.

---

## 5. SSE listener

To answer Unique chat messages from your own machine, set `SSE_ENABLED=true` and `SSE_URL` (the Unique Event
Socket URL) in `.env`, then start the app. It then receives Unique chat events and replies in the chat. If you
run the app on another port, change the port in `SSE_WEBHOOK_URL` too.

---

## 6. More documentation

[PROJECT_GUIDE.md](PROJECT_GUIDE.md) has the full details: all settings, the agent workflow, tracing in Arize AX
and running with Docker.
