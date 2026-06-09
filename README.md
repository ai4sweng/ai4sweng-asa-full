# KIO1 — AI Software Engineering Platform

An agentic platform that orchestrates a pipeline of AI-powered microservices (KIO shells) to autonomously analyse, test, and patch software repositories. Built with LangGraph, NATS JetStream, FastAPI, PostgreSQL, and React.

---

## Table of Contents

- [Overview](#overview)
- [Quick Start — Local](#quick-start--local)
- [Quick Start — Docker](#quick-start--docker)
- [Service Map](#service-map)
- [API Reference](#api-reference)
- [LLM Configuration](#llm-configuration)
- [KIO Pipeline](#kio-pipeline)
- [Project Structure](#project-structure)
- [Development Workflow](#development-workflow)
- [Environment Variables](#environment-variables)

---

## Overview

```
User → Dashboard (React) → Orchestrator (LangGraph) → KIO2 … KIO13 → Artifacts
                                  ↕                        ↕
                          Session Manager            NATS JetStream
                          (PostgreSQL)               (durable queue)
```

**What it does:**
1. User describes a task ("Find SQL injection bugs in this repo") via the dashboard or REST API
2. The Orchestrator uses an LLM to plan which KIO agents to run, or uses an explicit sequence
3. Each KIO performs one step (repo scan, bug detection, test generation, patch, etc.)
4. Human-in-the-Loop (HITL) checkpoints pause the workflow at configurable steps for review
5. All artifacts, checkpoints, and workflow state are persisted to PostgreSQL
6. The React dashboard streams live progress via Server-Sent Events

---

## Quick Start — Local

### Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.12+ | |
| [uv](https://docs.astral.sh/uv/) | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Docker Desktop | 4.x+ | For postgres + NATS only |
| Ollama | latest | `brew install ollama` or [ollama.com](https://ollama.com) |

### 1. Clone and install

```bash
git clone <repo-url> EnisAliMerge
cd EnisAliMerge
uv sync --all-packages
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` — required fields:

```bash
JWT_SECRET_KEY=<run: openssl rand -hex 32>
LLM_PROVIDER=ollama          # or: mock (no Ollama needed), openai, claude
```

### 3. Start infrastructure

```bash
docker compose up postgres nats -d
```

### 4. Run database migrations

```bash
cd shared && alembic upgrade head && cd ..
```

### 5. Pull the LLM model

```bash
ollama pull qwen2.5-coder:3b
```

### 6. Start all services

```bash
bash run_all.sh
```

Or start individually in separate terminals:

```bash
PYTHONPATH=. uvicorn apps.session_manager.main:app --port 8002
PYTHONPATH=. uvicorn apps.lm_engine.main:app --port 8001
PYTHONPATH=. uvicorn apps.orchestrator.main:app --port 8000
PYTHONPATH=. uvicorn apps.kio_shells.kio3.main:app --port 8013
PYTHONPATH=. uvicorn apps.kio_shells.kio5.main:app --port 8015
# … add other KIOs as needed
```

### 7. Register a user and run a workflow

```bash
# Register
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"secret123"}'

# Login
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"secret123"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Run a workflow
curl -X POST http://localhost:8000/workflow/run \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Find security bugs in the repo",
    "kio_sequence": ["kio3","kio5"],
    "working_directory": "./examples/buggy_fastapi_repo"
  }'
```

---

## Quick Start — Docker

```bash
cp .env.example .env
# Set JWT_SECRET_KEY in .env

docker compose up --build
```

Services start in dependency order. The dashboard is available at **http://localhost:3000**.

Default credentials after first run: register via `POST /auth/register` or through the dashboard login screen.

```bash
# Rebuild a single service after code change
docker compose build orchestrator && docker compose up orchestrator -d

# View logs
docker compose logs -f orchestrator
docker compose logs -f kio3

# Stop everything
docker compose down
```

---

## Service Map

| Service | Port (local) | Port (Docker) | Description |
|---|---|---|---|
| **Orchestrator** | 8000 | 8000 | LangGraph workflow engine, REST + SSE API, JWT auth |
| **LM Engine** | 8001 | 8001 | LLM proxy — Ollama / OpenAI / Claude |
| **Session Manager** | 8002 | 8002 | PostgreSQL-backed session, artifact, HITL store |
| **Dashboard** | — | 3000 | React + Tailwind + Zustand UI (nginx in Docker) |
| **PostgreSQL** | 5432 | 5432 | System of record |
| **NATS** | 4222 | 4222 | JetStream message bus (NATS monitoring on 8222) |
| **KIO2** | 8012 | 8012 | Requirements Analysis |
| **KIO3** | 8013 | 8013 | Repository Analyzer (real LLM logic) |
| **KIO4** | 8014 | 8014 | Test Generation |
| **KIO5** | 8015 | 8015 | Bug Detector (real LLM logic) |
| **KIO6** | 8016 | 8016 | Patch Generator |
| **KIO7** | 8017 | 8017 | Code Reviewer |
| **KIO8** | 8018 | 8018 | Security Auditor |
| **KIO9** | 8019 | 8019 | Documentation Generator |
| **KIO10** | 8020 | 8020 | Dependency Analyser |
| **KIO11** | 8021 | 8021 | Performance Profiler |
| **KIO12** | 8022 | 8022 | Deployment Validator |
| **KIO13** | 8023 | 8023 | Evidence Reporter |

Health check for any service: `GET /health/`

---

## API Reference

All `/workflow/*` and `/mcp/*` endpoints require `Authorization: Bearer <jwt>`.

### Auth

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/auth/register` | Create account `{username, password, email?}` → 201 |
| `POST` | `/auth/login` | Get JWT `{username, password}` → `{access_token, expires_in}` |
| `GET` | `/auth/me` | Current user info |

### Workflow

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/workflow/run` | Start workflow → 202 `{session_id}` |
| `GET` | `/workflow/{session_id}/status` | Live status, progress, artifacts |
| `POST` | `/workflow/{session_id}/approve` | Resolve HITL checkpoint `{feedback?}` |
| `GET` | `/workflow/events?token=<jwt>` | SSE stream (use `?token=` for EventSource) |

**Run workflow body:**

```json
{
  "workflow_id": "optional-uuid",
  "kio_sequence": ["kio3", "kio5"],
  "hitl_after": ["kio3"],
  "description": "Scan for SQL injection",
  "working_directory": "/path/to/repo"
}
```

- `kio_sequence`: explicit order; omit to let the LLM plan it
- `hitl_after`: force a human review after these KIOs (in addition to any KIO-driven REVIEW_REQUIRED)

### MCP Tools

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/mcp/tools` | List all registered tools |
| `POST` | `/mcp/tools/call` | Execute a tool `{name, arguments}` |

Built-in tools: `filesystem.read_file`, `filesystem.list_directory`, `filesystem.write_file`, `shell.run_command`

### Interactive docs

```
http://localhost:8000/docs      # Swagger UI
http://localhost:8000/redoc     # ReDoc
```

---

## LLM Configuration

Set `LLM_PROVIDER` in `.env`:

| Provider | Env value | Required extras |
|---|---|---|
| Ollama (default) | `ollama` | Ollama running locally; `ollama pull qwen2.5-coder:3b` |
| Mock (instant, no LLM) | `mock` | Nothing — useful for development/CI |
| OpenAI | `openai` | `OPENAI_API_KEY=sk-...` |
| Anthropic Claude | `claude` | `ANTHROPIC_API_KEY=sk-ant-...` |

```bash
# Switch to mock for local dev without Ollama
LLM_PROVIDER=mock

# Switch to OpenAI
LLM_PROVIDER=openai
OPENAI_MODEL=gpt-4o
OPENAI_API_KEY=sk-...

# Switch to Claude
LLM_PROVIDER=claude
ANTHROPIC_MODEL=claude-sonnet-4-6
ANTHROPIC_API_KEY=sk-ant-...
```

The platform handles JSON parsing robustness automatically — `extract_json_object()` repairs markdown fences, Python literals, truncated output, and wrong field names before the KIO handler ever sees the result.

---

## KIO Pipeline

The default pipeline for repository auditing:

```
KIO3 (Repo Analyzer)
  └─► HITL: "Found N issues. Proceed?"
      └─► KIO5 (Bug Detector)
            └─► HITL: "Found N bugs. Approve patch?"
                  └─► KIO6 (Patch Generator)  [placeholder]
                        └─► KIO4 (Test Generation)  [placeholder]
                              └─► KIO13 (Evidence Reporter)  [placeholder]
```

KIO3 and KIO5 contain full LLM logic. KIO2, KIO4, KIO6–KIO13 are currently placeholder implementations awaiting team delivery — see `KIO_DEVELOPER_GUIDE.md`.

**Transport:** Orchestrator → NATS JetStream → KIO (durable, at-least-once). Falls back to HTTP POST `/execute` if NATS is unavailable (`USE_NATS=false`).

---

## Project Structure

```
EnisAliMerge/
├── apps/
│   ├── orchestrator/            # LangGraph engine + JWT auth + REST API
│   │   ├── main.py              # FastAPI app, lifespan, router registration
│   │   ├── src/
│   │   │   ├── api/
│   │   │   │   ├── router.py    # /workflow/* endpoints
│   │   │   │   ├── auth_router.py
│   │   │   │   └── mcp_router.py
│   │   │   ├── engine/
│   │   │   │   ├── workflow_runner.py   # WorkflowRunner singleton
│   │   │   │   ├── workflow_graph.py    # LangGraph StateGraph builder
│   │   │   │   ├── graph_nodes.py      # plan/run_kio/hitl/advance/complete nodes
│   │   │   │   ├── graph_state.py      # WorkflowGraphState TypedDict
│   │   │   │   ├── checkpointer.py     # AsyncPostgresSaver factory
│   │   │   │   └── event_bus.py        # In-process SSE bus
│   │   │   └── services/
│   │   │       ├── kio_client.py       # JetStream + HTTP KIO dispatcher
│   │   │       ├── lm_client.py        # LM Engine HTTP client
│   │   │       └── session_client.py   # Session Manager HTTP client
│   │   └── pyproject.toml
│   │
│   ├── lm_engine/               # LLM proxy service
│   │   ├── main.py
│   │   └── src/api/router.py    # POST /llm/complete
│   │
│   ├── session_manager/         # Session + artifact + HITL store
│   │   ├── main.py
│   │   └── src/
│   │       ├── api/router.py    # /sessions/* REST API
│   │       └── service/session_service.py
│   │
│   ├── kio_shells/
│   │   ├── kio_base.py          # make_kio_app() factory (shared, do not edit)
│   │   ├── Dockerfile           # Single image for all KIOs (ARG KIO_ID)
│   │   ├── kio2/ … kio13/       # One main.py per KIO
│   │   └── pyproject.toml
│   │
│   └── dashboard/               # React + Vite + Tailwind + Zustand
│       ├── src/
│       ├── Dockerfile
│       └── nginx.conf
│
├── shared/                      # Shared library (all services depend on this)
│   ├── config.py                # Pydantic Settings — all env vars
│   ├── constants.py             # Enums, protocol version, artifact types
│   ├── auth/
│   │   ├── jwt_handler.py       # PyJWT encode/decode
│   │   ├── dependencies.py      # FastAPI get_current_user dependency
│   │   └── schemas.py
│   ├── contracts/
│   │   └── kio_envelope.py      # KIOEnvelope Pydantic model
│   ├── llm/
│   │   ├── factory.py           # create_llm_provider()
│   │   ├── ollama_provider.py
│   │   ├── openai_provider.py
│   │   ├── claude_provider.py
│   │   ├── mock.py
│   │   └── llm_json_coerce.py   # Hallucination-resilient JSON parsing
│   ├── messaging/
│   │   └── jetstream.py         # JetStreamManager (NATS pub/sub/request-reply)
│   ├── persistence/
│   │   ├── models.py            # SQLAlchemy ORM models
│   │   ├── repositories.py      # Async Repository pattern
│   │   ├── database.py          # Engine + session factory
│   │   └── session_provider.py  # Unit-of-work context managers
│   ├── migrations/              # Alembic migrations
│   │   └── versions/
│   │       ├── 0001_initial_schema.py
│   │       └── 0002_add_users.py
│   ├── mcp/
│   │   ├── registry.py          # MCPToolRegistry
│   │   └── tools/
│   │       ├── filesystem.py    # read_file, write_file, list_directory
│   │       └── shell.py         # run_command
│   ├── a2a/
│   │   └── client.py            # A2AClient (KIO-to-KIO direct calls)
│   └── observability/
│       └── langfuse_client.py   # Optional Langfuse tracing
│
├── examples/
│   └── buggy_fastapi_repo/      # Target repo used by KIO3/KIO5 by default
│
├── .env.example                 # Copy to .env
├── docker-compose.yml           # Full 25-service stack
├── run_all.sh                   # Local dev process launcher
├── pyproject.toml               # uv workspace root
├── README.md                    # This file
├── Architecture.md              # Detailed system architecture
└── KIO_DEVELOPER_GUIDE.md       # Guide for KIO implementers
```

---

## Development Workflow

### Adding or editing a KIO

1. Edit `apps/kio_shells/kioN/main.py` — replace `placeholder_handler` with your `async def handler(envelope)`
2. Read `KIO_DEVELOPER_GUIDE.md` for the full handler contract
3. Test directly: `curl -X POST http://localhost:{port}/execute -d '{...}'`
4. Run through orchestrator: `POST /workflow/run` with `"kio_sequence": ["kioN"]`

### Schema migrations

```bash
cd shared
alembic revision --autogenerate -m "describe_your_change"
alembic upgrade head
```

### Running tests

```bash
PYTHONPATH=. pytest tests/ -v
PYTHONPATH=. pytest tests/ -v --cov=shared --cov=apps
```

### Linting

```bash
uv run ruff check .
uv run ruff format .
```

### Switching transport (NATS ↔ HTTP)

```bash
# Disable NATS (HTTP-only — no NATS server needed)
USE_NATS=false

# Enable NATS (default)
USE_NATS=true
```

---

## Environment Variables

See `.env.example` for the full list. Key variables:

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://…@localhost:5432/enisalimerge` | PostgreSQL DSN |
| `NATS_URL` | `nats://localhost:4222` | NATS server |
| `JWT_SECRET_KEY` | `change-me-in-production` | **Must override in production** |
| `JWT_EXPIRE_MINUTES` | `60` | Token lifetime |
| `LLM_PROVIDER` | `ollama` | `ollama` \| `mock` \| `openai` \| `claude` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `qwen2.5-coder:3b` | Model name |
| `OPENAI_API_KEY` | — | Required when `LLM_PROVIDER=openai` |
| `ANTHROPIC_API_KEY` | — | Required when `LLM_PROVIDER=claude` |
| `USE_NATS` | `true` | `false` = HTTP-only mode (no NATS needed) |
| `KIO_BASE_HOST` | `localhost` | Empty string in Docker (uses container DNS) |
| `HITL_APPROVAL_TIMEOUT` | `300` | Seconds before HITL auto-expires |
| `TARGET_REPO_PATH` | `examples/buggy_fastapi_repo` | Default repo for KIO3/KIO5 |

---

## Related Documents

- [`Architecture.md`](./Architecture.md) — Detailed component and data-flow diagrams
- [`KIO_DEVELOPER_GUIDE.md`](./KIO_DEVELOPER_GUIDE.md) — Guide for teams implementing KIO2–KIO13
