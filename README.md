# KIO1 — AI Software Engineering Platform

An agentic platform that orchestrates a pipeline of AI-powered microservices (KIO shells) to autonomously analyse, generate, test, and patch software. Built with LangGraph, NATS JetStream, FastAPI, PostgreSQL, and React.

---

## Table of Contents

- [Overview](#overview)
- [Technologies](#technologies)
- [Quick Start — Docker](#quick-start--docker)
- [Quick Start — Local](#quick-start--local)
- [Service Map](#service-map)
- [KIO Agents](#kio-agents)
- [API Reference](#api-reference)
- [LLM Configuration](#llm-configuration)
- [LLM Fallback (HITL-driven)](#llm-fallback-hitl-driven)
- [Project Structure](#project-structure)
- [Development Workflow](#development-workflow)
- [Environment Variables](#environment-variables)

---

## Overview

```
User (natural language prompt)
       │
       ▼
  POST /workflow/prompt
       │
       ▼
 KIO1 — Prompt Router          ← decides which agents to run
       │
       ├─► KIO9  Code Generator        (when code must be written)
       ├─► KIO3  Repository Analyzer   (when a repo must be read)
       ├─► KIO4  Test Generator
       ├─► KIO5  Bug Detector ─────────────────────────── A2A ──► KIO12 OWASP Scanner
       ├─► KIO6  Patch Generator
       ├─► KIO7  Test Re-runner
       └─► KIO8  Evidence Reporter
              │
              ▼
     HITL checkpoint (human review at configurable steps)
              │
              ▼
       Session Manager (PostgreSQL) — all artifacts persisted
```

**What it does:**
1. User sends a natural-language prompt (and optional code snippet) via `/workflow/prompt`
2. **KIO1 (Prompt Router)** uses an LLM to decide which agents to run and in what order
3. Each KIO performs one step — code generation, repo analysis, bug detection, patching, etc.
4. **Human-in-the-Loop (HITL)** checkpoints pause the workflow at configurable steps for review
5. **Agent-to-Agent (A2A)**: KIO5 calls KIO12 directly for OWASP Top 10 enrichment without going through the orchestrator
6. **LLM Fallback**: if qwen7b fails on any step, HITL asks the user to approve a retry with Claude
7. All artifacts, checkpoints, and workflow state are persisted to PostgreSQL and streamed via SSE

---

## Technologies

### Workflow Orchestration

| Technology | Role |
|---|---|
| **[LangGraph](https://github.com/langchain-ai/langgraph)** | Core workflow engine. The KIO pipeline is modelled as a `StateGraph` with named nodes (`plan → run_kio → hitl → advance → complete`) and conditional edges. Supports interrupt-and-resume for HITL checkpoints. |
| **[AsyncPostgresSaver](https://github.com/langchain-ai/langgraph)** | LangGraph's PostgreSQL checkpointer (`langgraph-checkpoint-postgres`). Persists the full graph state after every node — workflow state survives process restarts. Uses three tables: `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`. |

### Messaging

| Technology | Role |
|---|---|
| **[NATS](https://nats.io)** | High-performance cloud-native messaging system. Used as the inter-service transport between the orchestrator and KIO shells. |
| **[NATS JetStream](https://docs.nats.io/nats-concepts/jetstream)** | Persistent, at-least-once message delivery layer on top of NATS. The orchestrator publishes `JOB_REQUEST` messages to per-KIO subjects (`kio.kio5.request`); KIO shells subscribe with durable consumers and reply on ephemeral reply subjects. JetStream guarantees redelivery on consumer failure and prevents message loss between restarts. ACK-before-reply ordering is intentional — avoids duplicate LLM calls on redelivery. |
| **SSE (Server-Sent Events)** | Real-time event streaming from the orchestrator to the frontend. Each authenticated user gets an isolated queue; events are filtered by session owner before delivery. Implemented via FastAPI `StreamingResponse` with `text/event-stream`. |
| **JSON** | Wire format for all inter-service messages. `MessageEnvelope` (JOB_REQUEST / JOB_RESULT) is serialized to JSON before being published to NATS JetStream and deserialized by KIO consumers. REST request/response bodies, SSE event payloads, LLM prompts, and artifact data are all JSON. `llm_json_coerce.py` in `shared/llm/` handles hallucination-resilient JSON parsing when LLM output is malformed. |

### API & Web Framework

| Technology | Role |
|---|---|
| **[FastAPI](https://fastapi.tiangolo.com)** | Async REST API framework used by every service (orchestrator, session manager, LM engine, all KIO shells). Provides automatic OpenAPI docs at `/docs`. |
| **[Uvicorn](https://www.uvicorn.org)** | ASGI server that hosts FastAPI. Used directly in Docker (`uvicorn main:app`). |
| **[Pydantic v2](https://docs.pydantic.dev/latest/)** | Data validation for all API schemas and service configuration. `pydantic-settings` loads `Settings` from environment variables with validation on startup (e.g. JWT secret strength check). |

### Authentication & Security

| Technology | Role |
|---|---|
| **[PyJWT](https://pyjwt.readthedocs.io)** | HS256 JWT token generation and validation. The orchestrator issues tokens on login; all `/workflow/*` endpoints verify the Bearer header. |
| **[bcrypt](https://pypi.org/project/bcrypt/)** | Password hashing for the user store. Passwords are never stored in plain text. |

### Database & Persistence

| Technology | Role |
|---|---|
| **[PostgreSQL 16](https://www.postgresql.org)** | Primary database for sessions, artifacts, HITL checkpoints, users, and LangGraph graph state. |
| **[SQLAlchemy 2 (async)](https://docs.sqlalchemy.org)** | Async ORM for the session manager. Uses `AsyncSession` + `asyncpg` driver. |
| **[psycopg3 + psycopg-pool](https://www.psycopg.org/psycopg3/)** | PostgreSQL driver used specifically by the LangGraph `AsyncPostgresSaver`. A dedicated `AsyncConnectionPool` is maintained separate from the application pool. |
| **[Alembic](https://alembic.sqlalchemy.org)** | Database schema migration tool. Migrations live in `shared/migrations/`. |
| **[asyncpg](https://magicstack.github.io/asyncpg/)** | High-performance async PostgreSQL driver used by the SQLAlchemy session manager pool. |

### LLM / AI Providers

| Technology | Role |
|---|---|
| **[Ollama](https://ollama.com)** | Runs local LLMs on the host machine. Default primary provider. The platform is tested with `qwen2.5-coder:7b` but any Ollama-compatible model works. Accessed from Docker via `host.docker.internal:11434`. |
| **[Anthropic Claude](https://www.anthropic.com)** | Cloud LLM provider. Used as the HITL-driven fallback when Ollama fails. Default model: `claude-haiku-4-5-20251001`. |
| **[OpenAI](https://platform.openai.com)** | Alternative cloud LLM provider. Supported alongside Anthropic via the shared `LLMProvider` abstraction. |
| **[Langfuse](https://langfuse.com)** | Optional LLM observability and tracing. Wraps provider calls to record prompts, completions, latency, and cost. Disabled by default (`LANGFUSE_SECRET_KEY` not set). |

### Frontend

| Technology | Role |
|---|---|
| **[React 18](https://react.dev)** | Component-based UI framework for the operator dashboard. |
| **[Vite](https://vitejs.dev)** | Frontend build tool and dev server. Produces optimised static assets for the Docker image. |
| **[Tailwind CSS](https://tailwindcss.com)** | Utility-first CSS framework used for all dashboard styling. |
| **[Zustand](https://zustand-demo.pmnd.rs)** | Lightweight client-side state manager. Holds workflow status, HITL checkpoint state, and SSE event log in a single reactive store. |
| **[nginx](https://nginx.org)** | Serves the built React app as static files inside the `dashboard` Docker container. |

### Infrastructure & Tooling

| Technology | Role |
|---|---|
| **[Docker / Docker Compose](https://docs.docker.com/compose/)** | All services run as containers. A single `docker compose up --build` starts the full stack. KIO shells share a single Dockerfile (`ARG KIO_ID` selects the entry point). |
| **[Python 3.12](https://www.python.org)** | Runtime for all backend services. Requires 3.12+ for `asyncio.timeout()` and `ExceptionGroup`. |
| **[uv](https://docs.astral.sh/uv/)** | Fast Python package manager and virtual environment tool. Used instead of pip for local development. |

### Protocols & Patterns

| Technology | Role |
|---|---|
| **A2A (Agent-to-Agent)** | Custom HTTP protocol for direct KIO-to-KIO calls without routing through the orchestrator graph. KIO5 calls KIO12 via `A2AClient.invoke()`, sharing `session_id`, `workflow_id`, and `llm_provider_override`. |
| **MCP (Model Context Protocol)** | Tool registry exposed at `/mcp/tools`. Provides KIOs with structured access to filesystem operations and shell commands. Built-in tools: `filesystem.read_file`, `filesystem.list_directory`, `filesystem.write_file`, `shell.run_command`. |
| **HITL (Human-in-the-Loop)** | Workflow pause-and-resume pattern implemented via LangGraph's `interrupt()`. The graph pauses at a checkpoint; the operator reviews and calls `POST /workflow/{id}/approve`; the graph resumes via `Command(resume=feedback)`. |

---

## Quick Start — Docker

```bash
git clone <repo-url> kio1-platform
cd kio1-platform

cp .env.example .env
# Edit .env — required: JWT_SECRET_KEY (see below)
# Optional: set ANTHROPIC_API_KEY for Claude fallback

docker compose up --build
```

The dashboard is available at **http://localhost:3000**.  
Swagger UI: **http://localhost:8000/docs**

```bash
# Rebuild a single service after a code change
docker compose build kio5 && docker compose up kio5 -d

# View logs
docker compose logs -f orchestrator
docker compose logs -f kio5

# Stop everything
docker compose down
```

---

## Quick Start — Local

### Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.12+ | |
| [uv](https://docs.astral.sh/uv/) | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Docker Desktop | 4.x+ | For postgres + NATS |
| Ollama | latest | `brew install ollama` or [ollama.com](https://ollama.com) |

### 1. Clone and install

```bash
git clone <repo-url> kio1-platform
cd kio1-platform
uv sync --all-packages
```

### 2. Configure environment

```bash
cp .env.example .env
```

Minimum required in `.env`:

```bash
JWT_SECRET_KEY=$(openssl rand -hex 32)
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen2.5-coder:7b
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
ollama pull qwen2.5-coder:7b
```

### 6. Start all services

```bash
bash run_all.sh
```

Or individually in separate terminals:

```bash
PYTHONPATH=. uvicorn apps.session_manager.main:app --port 8002
PYTHONPATH=. uvicorn apps.lm_engine.main:app --port 8001
PYTHONPATH=. uvicorn apps.orchestrator.main:app --port 8000
PYTHONPATH=. uvicorn apps.kio_shells.kio1.main:app --port 8011
PYTHONPATH=. uvicorn apps.kio_shells.kio5.main:app --port 8015
PYTHONPATH=. uvicorn apps.kio_shells.kio12.main:app --port 8022
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
  -d '{"username":"admin","password":"secret123"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Prompt-driven workflow (recommended)
curl -X POST http://localhost:8000/workflow/prompt \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "bug bul",
    "code": "import sqlite3\ndef get_user(name):\n    conn.execute(\"SELECT * FROM users WHERE name=\" + name)\n"
  }'

# Explicit pipeline (advanced)
curl -X POST http://localhost:8000/workflow/run \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Scan repo for SQL injection",
    "kio_sequence": ["kio3", "kio5"],
    "hitl_after": ["kio5"],
    "working_directory": "./examples/buggy_fastapi_repo"
  }'
```

---

## Service Map

| Service | Port | Description |
|---|---|---|
| **Orchestrator** | 8000 | LangGraph workflow engine, REST + SSE API, JWT auth |
| **LM Engine** | 8001 | LLM proxy — Ollama / OpenAI / Claude |
| **Session Manager** | 8002 | PostgreSQL-backed session, artifact, HITL store |
| **Dashboard** | 3000 | React + Tailwind + Zustand UI (nginx in Docker) |
| **PostgreSQL** | 5432 | System of record |
| **NATS** | 4222 | JetStream message bus (monitoring on 8222) |
| **KIO1** | 8011 | Prompt Router |
| **KIO2** | 8012 | Planning Agent |
| **KIO3** | 8013 | Repository Analyzer |
| **KIO4** | 8014 | Test Generator |
| **KIO5** | 8015 | Bug Detector (+ A2A → KIO12) |
| **KIO6** | 8016 | Patch Generator |
| **KIO7** | 8017 | Test Re-runner |
| **KIO8** | 8018 | Evidence Report Agent |
| **KIO9** | 8019 | Code Generator |
| **KIO10** | 8020 | Energy-Efficiency / TinyML Agent |
| **KIO11** | 8021 | AI-Powered Test Automation |
| **KIO12** | 8022 | OWASP Security Scanner |
| **KIO13** | 8023 | Developer Training Agent |

Health check for any service: `GET /health/`

---

## KIO Agents

### KIO1 — Prompt Router

Reads a natural-language prompt and optional code snippet, then returns a `kio_sequence` that the orchestrator executes dynamically.

| Prompt pattern | Pipeline |
|---|---|
| "bug bul" / "find bugs" (+ code) | kio1 → kio5 |
| "kod yaz" / "write code" (no code) | kio1 → kio9 → kio5 |
| "fix" / "patch" (+ code) | kio1 → kio5 → kio6 → kio7 |
| "test yaz" / "generate tests" | kio1 → kio4 |
| "analiz et" / "analyze repo" | kio1 → kio3 → kio5 |
| "full pipeline" / "hepsi" | kio1 → kio3 → kio4 → kio5 → kio6 → kio7 → kio8 |

### KIO2 — Planning Agent

Alternative to KIO1. Uses an LLM to plan the pipeline from a plain-text task description without requiring the `/workflow/prompt` endpoint. Outputs a `kio_sequence` consumed by the orchestrator.

### KIO3 — Repository Analyzer

Reads a real code repository from disk (mounted into the container). Chunks files, builds a context window within token limits, and extracts high-level findings (architecture, dependencies, risk areas).

### KIO4 — Test Generator

Generates pytest test files for confirmed bugs or code findings using structured `### path\n\`\`\`lang\n...\n\`\`\`` output format.

### KIO5 — Bug Detector

Performs LLM-powered security and logic bug analysis. Works in two modes:
- **Direct code mode**: raw code in `initial_context.code` → full vulnerability scan
- **Findings validation mode**: validates upstream findings from KIO3

Also calls **KIO12 via A2A** for OWASP Top 10 enrichment on the same code, adding CWE identifiers and hardened file suggestions to the artifact.

Always returns `REVIEW_REQUIRED` so a human approves the confirmed bug list before patching begins.

### KIO6 — Patch Generator

Generates code fixes for the confirmed bugs from KIO5. Reads the original source files from the mounted repo for context. Outputs patches in the `### path` format.

### KIO7 — Test Re-runner

Applies patches from KIO6 to a temporary copy of the repo, installs dependencies, runs pytest, and interprets results via LLM. Verifies that fixes don't break existing tests.

### KIO8 — Evidence Report Agent

Produces a final structured report (executive summary, risk matrix, remediation table) from test results and all upstream findings. Intended for audit trails and compliance evidence.

### KIO9 — Code Generator

Generates complete, working code from a natural-language description. Used when the user says "write me code for X" without providing existing code. Supports intentional bug injection for security training scenarios.

### KIO12 — OWASP Security Scanner

Identifies OWASP Top 10 vulnerabilities in code. Accepts either a repository path (full scan) or inline code via A2A from KIO5. Returns `vulnerabilities` (with CWE IDs) and `files` (hardened versions).

### KIO10, KIO11, KIO13

Specialised research agents:
- **KIO10**: Energy-efficiency and Model-Driven TinyML analysis
- **KIO11**: AI-Powered Test Automation Tool (TAT)
- **KIO13**: Developer training and onboarding for AI-powered workflows

---

## API Reference

All `/workflow/*` endpoints require `Authorization: Bearer <jwt>`.

### Auth

| Method | Endpoint | Body / Response |
|---|---|---|
| `POST` | `/auth/register` | `{username, password, email?}` → 201 |
| `POST` | `/auth/login` | `{username, password}` → `{access_token, expires_in}` |
| `GET` | `/auth/me` | Current user info |

### Orchestrator (no auth required)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/status` | Orchestrator state machine status — state, active_workflows, accepts_workflows |
| `GET` | `/agents` | Dynamically discovered KIO agents — their host, port, capabilities, staleness |

### Workflow

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/workflow/prompt` | Natural language → KIO1 routes automatically |
| `POST` | `/workflow/run` | Explicit pipeline with fixed `kio_sequence` |
| `GET` | `/workflow/{id}/status` | Live status, progress, artifacts |
| `POST` | `/workflow/{id}/approve` | Resolve HITL checkpoint |
| `GET` | `/workflow/events?token=<jwt>` | SSE stream (real-time progress) |

**`POST /workflow/prompt` body:**

```json
{
  "prompt": "bug bul ve düzelt",
  "code": "def login(user, pw):\n    query = 'SELECT * FROM users WHERE name=' + user\n    ...",
  "owner": "demo_user"
}
```

- `prompt`: natural language task description (Turkish or English)
- `code`: optional code snippet; if omitted and prompt asks for code, KIO9 generates it
- `owner`: user identifier (defaults to `"demo_user"`)

**`POST /workflow/run` body:**

```json
{
  "workflow_id": "optional-uuid",
  "kio_sequence": ["kio3", "kio5"],
  "hitl_after": ["kio5"],
  "description": "Scan for SQL injection",
  "working_directory": "/path/to/repo",
  "timeout_seconds": 600
}
```

- `timeout_seconds`: optional per-workflow deadline in seconds. The TimeoutMonitor cancels the session if it exceeds the deadline. Also forwarded to each KIO in the payload for per-task enforcement.

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

| Provider | Value | Required extras |
|---|---|---|
| Ollama (default) | `ollama` | Ollama running locally; `ollama pull qwen2.5-coder:7b` |
| Mock (no LLM) | `mock` | Nothing — fast CI/dev |
| OpenAI | `openai` | `OPENAI_API_KEY=sk-...` |
| Anthropic Claude | `anthropic` | `ANTHROPIC_API_KEY=sk-ant-...` |

```bash
# Ollama with qwen2.5-coder:7b (default)
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen2.5-coder:7b

# Claude Haiku (fast, good quality)
LLM_PROVIDER=anthropic
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
ANTHROPIC_API_KEY=sk-ant-...

# Mock for development
LLM_PROVIDER=mock
```

---

## LLM Fallback (HITL-driven)

The platform supports automatic fallback to a secondary LLM when the primary fails:

```bash
# .env
LLM_PROVIDER=ollama               # primary: qwen7b
LLM_PROVIDER_FALLBACK=anthropic   # fallback: Claude Haiku
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
```

**Flow:**

1. A KIO fails (Ollama timeout, connection error, bad output)
2. Orchestrator creates a HITL checkpoint: *"KIO5 failed with 'ollama'. Approve retry with 'anthropic'?"*
3. User approves via the dashboard or API
4. The **same KIO reruns** at the same pipeline step with `llm_provider_override=anthropic`
5. Subsequent KIOs in the pipeline also use Claude (override propagates)

```bash
# Approve a fallback HITL checkpoint
curl -X POST http://localhost:8000/workflow/{session_id}/approve \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"checkpoint_id": "<ckpt-id>", "feedback": "yes", "actor": "admin"}'
```

If `LLM_PROVIDER_FALLBACK` is not set, failed KIOs mark the session as `FAILED` immediately.

---

## Agent-to-Agent (A2A) Communication

KIOs can call peer KIOs directly without routing through the orchestrator graph:

```
KIO5 (Bug Detector)
  └─► A2AClient.invoke("kio12", payload={code: ...})
        └─► KIO12 (OWASP Scanner)
              └─► returns {vulnerabilities, files}
  └─► merges OWASP results into its own artifact_data
```

The A2A call shares the same `session_id` and `workflow_id` — all produced artifacts are attributed to the parent session. From the orchestrator's perspective, both KIO5 and KIO12 work completes as a single pipeline step.

---

## Project Structure

```
kio1-platform/
├── apps/
│   ├── orchestrator/            # LangGraph engine + JWT auth + REST API
│   │   ├── main.py
│   │   └── src/
│   │       ├── api/
│   │       │   ├── router.py         # /workflow/run, /workflow/prompt, HITL, SSE
│   │       │   ├── auth_router.py
│   │       │   └── mcp_router.py
│   │       └── engine/
│   │           ├── workflow_runner.py     # WorkflowRunner singleton (run/approve/cancel)
│   │           ├── workflow_graph.py      # LangGraph StateGraph builder
│   │           ├── graph_nodes.py         # plan/run_kio/hitl/advance/complete + fallback
│   │           ├── graph_state.py         # WorkflowGraphState TypedDict
│   │           ├── checkpointer.py        # AsyncPostgresSaver
│   │           ├── event_bus.py           # In-process SSE publisher
│   │           ├── orchestrator_state.py  # OrchestratorStateMachine (Slide 16)
│   │           ├── timeout_monitor.py     # Background deadline sweep (every 5s)
│   │           └── agent_registry.py      # Dynamic KIO endpoint discovery
│   │
│   ├── lm_engine/               # LLM proxy (POST /llm/complete)
│   ├── session_manager/         # Session + artifact + HITL store
│   │
│   ├── kio_shells/
│   │   ├── kio_base.py          # make_kio_app() shared factory
│   │   ├── Dockerfile           # Single image, all KIOs (ARG KIO_ID)
│   │   ├── kio1/  … kio13/      # One main.py per KIO
│   │   └── pyproject.toml
│   │
│   └── dashboard/               # React + Vite + Tailwind + Zustand
│
├── shared/                      # Shared library (imported by all services)
│   ├── config.py                # Pydantic Settings — all env vars
│   ├── llm/
│   │   ├── factory.py           # create_llm_provider(override="")
│   │   ├── ollama_provider.py
│   │   ├── claude_provider.py
│   │   ├── openai_provider.py
│   │   ├── mock.py
│   │   └── llm_json_coerce.py   # Hallucination-resilient JSON parsing
│   ├── a2a/
│   │   └── client.py            # A2AClient — KIO-to-KIO direct invocation
│   ├── messaging/
│   │   └── jetstream.py         # NATS JetStream pub/sub/request-reply
│   ├── persistence/
│   │   ├── models.py            # SQLAlchemy ORM
│   │   ├── repositories.py
│   │   └── database.py
│   ├── migrations/              # Alembic migrations
│   ├── auth/                    # JWT + bcrypt
│   ├── mcp/                     # MCP tool registry
│   └── observability/           # Langfuse tracing (optional)
│
├── examples/
│   └── buggy_fastapi_repo/      # Demo repo for KIO3/KIO5
│
├── .env.example
├── docker-compose.yml
├── run_all.sh
├── Architecture.md              # Detailed architecture diagrams
└── KIO_DEVELOPER_GUIDE.md       # Guide for implementing new KIOs
```

---

## Development Workflow

### Adding or editing a KIO

1. Edit `apps/kio_shells/kioN/main.py`
2. Read `KIO_DEVELOPER_GUIDE.md` for the full handler contract
3. Test directly: `curl -X POST http://localhost:{port}/execute -d '{...}'`
4. Rebuild: `docker compose build kioN && docker compose up kioN -d`

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

### Switching transport (NATS ↔ HTTP)

```bash
# HTTP-only (no NATS server needed)
USE_NATS=false
```

---

## Environment Variables

See `.env.example` for the full list. Key variables:

| Variable | Default | Description |
|---|---|---|
| `JWT_SECRET_KEY` | `change-me-in-production` | **Must override in production** |
| `JWT_EXPIRE_MINUTES` | `60` | Token lifetime |
| `LLM_PROVIDER` | `ollama` | `ollama` \| `mock` \| `openai` \| `anthropic` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Primary model |
| `LLM_PROVIDER_FALLBACK` | `` | Secondary provider on failure (e.g. `anthropic`) |
| `ANTHROPIC_API_KEY` | — | Required when provider is `anthropic` |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Claude model name |
| `OPENAI_API_KEY` | — | Required when `LLM_PROVIDER=openai` |
| `DATABASE_URL` | `postgresql+asyncpg://…` | PostgreSQL DSN |
| `NATS_URL` | `nats://localhost:4222` | NATS server |
| `USE_NATS` | `true` | `false` = HTTP-only mode |
| `KIO_BASE_HOST` | `localhost` | Empty string in Docker (uses container DNS) |
| `KIO_CLIENT_TIMEOUT` | `300` | Seconds to wait for a KIO response |
| `HITL_APPROVAL_TIMEOUT` | `300` | Seconds before HITL auto-expires |
| `TARGET_REPO_PATH` | `examples/buggy_fastapi_repo` | Default repo for KIO3/KIO5 |

---

## Related Documents

- [`Architecture.md`](./Architecture.md) — Full component diagrams, data-flow, A2A protocol, HITL internals
- [`KIO_DEVELOPER_GUIDE.md`](./KIO_DEVELOPER_GUIDE.md) — Implementing new KIO agents
