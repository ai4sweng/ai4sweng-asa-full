# Architecture — KIO1 AI Software Engineering Platform

**Version:** Phase 10 (Orchestrator SM + Timeout Monitor + Dynamic Agent Discovery)  
**Stack:** Python 3.12 · FastAPI · LangGraph · NATS JetStream · PostgreSQL 16 · React 18

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Component Diagram](#2-component-diagram)
3. [Request Lifecycle — Workflow Run](#3-request-lifecycle--workflow-run)
4. [Orchestrator — LangGraph Workflow Engine](#4-orchestrator--langgraph-workflow-engine)
5. [Transport Layer — NATS JetStream + HTTP Fallback](#5-transport-layer--nats-jetstream--http-fallback)
6. [KIO Shell Architecture](#6-kio-shell-architecture)
7. [Session Manager — Persistence Layer](#7-session-manager--persistence-layer)
8. [LM Engine — LLM Proxy](#8-lm-engine--llm-proxy)
9. [Authentication & Security](#9-authentication--security)
10. [Persistence Schema](#10-persistence-schema)
11. [LLM Integration & Hallucination Recovery](#11-llm-integration--hallucination-recovery)
12. [Human-in-the-Loop (HITL)](#12-human-in-the-loop-hitl)
13. [Real-Time Streaming — SSE](#13-real-time-streaming--sse)
14. [MCP Tools](#14-mcp-tools)
15. [Agent-to-Agent (A2A) Protocol](#15-agent-to-agent-a2a-protocol)
16. [Observability](#16-observability)
17. [Docker Deployment](#17-docker-deployment)
18. [Design Decisions & Trade-offs](#18-design-decisions--trade-offs)
19. [Prompt Router & Full Platform Diagram](#19-prompt-router--full-platform-diagram)
20. [Orchestrator State Machine](#20-orchestrator-state-machine)
21. [Timeout Monitor](#21-timeout-monitor)
22. [Dynamic Agent Discovery](#22-dynamic-agent-discovery)

---

## 1. System Overview

The platform is a **multi-agent pipeline** where each agent (KIO) is an independent microservice. The Orchestrator coordinates them using LangGraph's stateful graph execution model — a fundamentally different approach from a simple for-loop: the graph state is persisted to PostgreSQL so any mid-execution crash or HITL pause survives process restarts.

```
┌──────────────────────────────────────────────────────────────────┐
│                        External Clients                          │
│              Dashboard (React)  │  REST API  │  MCP Clients      │
└───────────────────┬─────────────┴──────┬─────┴────────┬──────────┘
                    │ HTTP/SSE           │              │
                    ▼                   ▼              ▼
┌───────────────────────────────────────────────────────────────────┐
│                    Orchestrator  :8000                            │
│  ┌─────────────┐  ┌───────────────────┐  ┌───────────────────┐   │
│  │  Auth API   │  │  Workflow API      │  │   MCP API         │   │
│  │ /auth/*     │  │  /workflow/*       │  │  /mcp/tools       │   │
│  └──────┬──────┘  └────────┬──────────┘  └────────┬──────────┘   │
│         │                  │                       │              │
│         ▼                  ▼                       ▼              │
│  ┌─────────────┐  ┌───────────────────┐  ┌───────────────────┐   │
│  │  PassLib    │  │  WorkflowRunner   │  │  MCPToolRegistry  │   │
│  │  JWT / bcrypt│  │  + LangGraph      │  │  (filesystem,     │   │
│  └─────────────┘  │    StateGraph      │  │   shell tools)    │   │
│                   └────────┬──────────┘  └───────────────────┘   │
│                            │                                      │
│               ┌────────────┼────────────┐                        │
│               ▼            ▼            ▼                        │
│         KioClient    LmEngineClient  SessionClient               │
└───────────┬───────────────┬────────────┬──────────────────────────┘
            │               │            │
     NATS / HTTP      HTTP POST    HTTP REST
            │               │            │
            ▼               ▼            ▼
    ┌──────────────┐  ┌──────────┐  ┌───────────────────┐
    │  KIO Shells  │  │ LM Engine│  │  Session Manager  │
    │  kio1–kio13  │  │  :8001   │  │  :8002            │
    └──────┬───────┘  └────┬─────┘  └────────┬──────────┘
           │               │                  │
           ▼               ▼                  ▼
    ┌──────────────┐  ┌──────────┐  ┌───────────────────┐
    │ NATS :4222   │  │  Ollama  │  │   PostgreSQL      │
    │ JetStream    │  │ /OpenAI  │  │   :5432           │
    └──────────────┘  │ /Claude  │  └───────────────────┘
                      └──────────┘
```

---

## 2. Component Diagram

### Orchestrator internals

```
apps/orchestrator/
│
├── main.py                 FastAPI app + lifespan
│   Lifespan order:
│     1. init_checkpointer()      — open PG connection pool for LangGraph
│     2. init_runner()            — build WorkflowRunner singleton, rehydrate()
│     3. get_orchestrator_sm()    — init OrchestratorStateMachine (INITIALIZING)
│     4. get_jetstream()          — connect to NATS (if USE_NATS=true)
│     5. subscribe kio.*.capability → AgentRegistry (dynamic discovery)
│     6. subscribe kio.*.status   → re-emit as TASK_PROGRESS SSE
│     7. get_timeout_monitor()    — start background deadline sweep (every 5s)
│
├── src/api/
│   ├── auth_router.py       /auth/register  /auth/login  /auth/me
│   ├── router.py            /workflow/run  /workflow/prompt  /{id}/status  /{id}/approve  /events
│   └── mcp_router.py        /mcp/tools  /mcp/tools/call
│   (root)                   GET /status  — OrchestratorStateMachine summary
│                            GET /agents  — list dynamically registered KIO agents
│
├── src/engine/
│   ├── workflow_runner.py      WorkflowRunner — run() / approve() / cancel() / get_state()
│   ├── workflow_graph.py       build_workflow_graph() → CompiledStateGraph
│   ├── graph_nodes.py          plan / run_kio / hitl / advance / complete nodes
│   ├── graph_state.py          WorkflowGraphState TypedDict
│   ├── checkpointer.py         AsyncPostgresSaver factory (falls back to MemorySaver)
│   ├── event_bus.py            In-process SSE pub/sub (one Queue per subscriber)
│   ├── orchestrator_state.py   OrchestratorStateMachine — Slide 16 state machine
│   ├── timeout_monitor.py      TimeoutMonitor — background sweep for timed-out sessions
│   └── agent_registry.py       AgentRegistry — dynamic KIO endpoint discovery
│
└── src/services/
    ├── kio_client.py        JetStream primary + HTTP fallback per KIO
    ├── lm_client.py         HTTP POST /llm/complete + hallucination-resilient parsing
    └── session_client.py    HTTP REST → Session Manager
```

### Shared library

```
shared/
├── config.py               Single source of truth for all env vars (Pydantic Settings)
├── constants.py            Enums: WorkflowState, TaskState, ArtifactType, NatsSubject
├── auth/                   JWT + bcrypt (PyJWT + passlib)
├── contracts/              KIOEnvelope — the inter-service message format
├── llm/
│   ├── factory.py          create_llm_provider() — selects Ollama/OpenAI/Claude/Mock
│   ├── ollama_provider.py  /api/chat with health_check()
│   ├── llm_json_coerce.py  extract_json_object() — multi-strategy JSON repair
│   └── observed.py         ObservedLLMProvider wraps any provider with Langfuse tracing
├── messaging/
│   └── jetstream.py        JetStreamManager — connect/ensure-stream/request-reply/subscribe
├── persistence/
│   ├── models.py           ORM: WorkflowRecord, TaskRecord, ArtifactRecord,
│   │                             HumanApprovalRecord, UserRecord, AgentRecord, …
│   ├── repositories.py     Repository — all CRUD + HITL + lineage operations
│   ├── database.py         Async engine + session factory (asyncpg)
│   └── session_provider.py Unit-of-work: session_scope() + read_scope()
├── migrations/             Alembic async migrations
├── mcp/                    MCPToolRegistry + filesystem + shell tools
└── a2a/client.py           A2AClient — KIO-to-KIO direct calls
```

---

## 3. Request Lifecycle — Workflow Run

```
Client                  Orchestrator              NATS            KIO3           SM / PG
  │                          │                     │                │                │
  │  POST /workflow/run       │                     │                │                │
  │─────────────────────────►│                     │                │                │
  │                          │  await sm.create_session()           │                │
  │                          │─────────────────────────────────────────────────────►│
  │                          │◄─────────────────────────────────────────────────────│
  │                          │                     │                │                │
  │  202 {session_id}        │                     │                │                │
  │◄─────────────────────────│                     │                │                │
  │                          │                     │                │                │
  │  GET /workflow/events     │                     │                │                │
  │─────────────────────────►│  (SSE stream open)  │                │                │
  │                          │                     │                │                │
  │ ◄─── SSE: PLANNING ──────│  asyncio.create_task(_run_graph())  │                │
  │                          │  graph.ainvoke(initial_input)       │                │
  │                          │  plan_node → lm_client.plan_workflow()               │
  │                          │                     │                │                │
  │ ◄─── SSE: KIO_STARTED ───│                     │                │                │
  │                          │  kio_client.execute("kio3", …)      │                │
  │                          │─────────────────────►│               │                │
  │                          │  (JetStream publish) │               │                │
  │                          │                     │ kio.kio3.request               │
  │                          │                     │───────────────►│                │
  │                          │                     │                │ handler()      │
  │                          │                     │                │ LLM scan …     │
  │                          │                     │                │                │
  │                          │                     │ _kio_reply.X  │                │
  │                          │◄────────────────────┤◄──────────────│                │
  │                          │                     │                │                │
  │                          │  sm.register_artifact()             │                │
  │                          │─────────────────────────────────────────────────────►│
  │                          │                     │                │                │
  │  ◄─── SSE: HITL_CHECKPOINT ──────────────────  │                │                │
  │                          │  graph paused via interrupt()       │                │
  │                          │  state → PG checkpoint              │                │
  │                          │                     │                │                │
  │  POST /{id}/approve       │                     │                │                │
  │─────────────────────────►│  sm.resolve_checkpoint()            │                │
  │                          │─────────────────────────────────────────────────────►│
  │  202                     │  graph.ainvoke(Command(resume=…))   │                │
  │◄─────────────────────────│  → next KIO …                       │                │
```

---

## 4. Orchestrator — LangGraph Workflow Engine

### Graph topology

```
START
  │
  ▼
[plan]          ← calls LM Engine to plan kio_sequence if not explicit
  │
  ▼
[run_kio]       ← dispatches current KIO via JetStream/HTTP
  │
  ▼ should_hitl()
  ├──► "hitl"   ── if KIO returned REVIEW_REQUIRED OR kio_id in hitl_after
  │     │
  │     │ interrupt()  ←── LangGraph pauses here; state written to PG
  │     │              ←── graph.ainvoke(Command(resume=feedback)) resumes it
  │     ▼
  └──► [advance]  ← increments current_step, clears HITL state
           │
           ▼ should_continue()
           ├──► "run_kio"   ← if more steps remain
           └──► [complete]  ← if all steps done → update SM to COMPLETED → END
```

### WorkflowGraphState

```python
class WorkflowGraphState(TypedDict):
    session_id:            str
    workflow_id:           str
    owner:                 str
    description:           str
    working_directory:     str
    kio_sequence:          list[str]
    hitl_after:            list[str]
    current_step:          int
    last_result:           dict[str, Any]
    artifacts:             Annotated[list[str], operator.add]   # append-only
    feedback:              str
    status:                str       # QUEUED→VALIDATING→READY→RUNNING→BLOCKED→COMPLETED/FAILED
    error:                 str | None
    pending_checkpoint_id: str | None
    initial_context:       dict      # code / prompt injected by /workflow/prompt
    correlation_id:        str       # shared across all messages in this invocation
    timeout_seconds:       int | None  # per-workflow deadline (enforced by TimeoutMonitor)
```

### Workflow status lifecycle

```
QUEUED        ← session created, graph task enqueued
VALIDATING    ← plan_node validating the kio_sequence
READY         ← plan_node complete, first KIO about to dispatch
RUNNING       ← run_kio_node dispatched a KIO (or HITL approved and resumed)
DISPATCHED    ← KIO request sent, awaiting reply
BLOCKED       ← HITL checkpoint paused the graph (human review needed)
COMPLETED     ← complete_node finished all steps successfully
FAILED        ← _handle_failure() or TimeoutMonitor.cancel() fired
```

### PostgreSQL Checkpointer

`checkpointer.py` wraps `langgraph-checkpoint-postgres` (`AsyncPostgresSaver`):

- Opens an `AsyncConnectionPool` (max 5 connections) pointing to the same PostgreSQL database
- Calls `checkpointer.setup()` on startup — creates `checkpoints`, `checkpoint_blobs`, `checkpoint_writes` tables (idempotent)
- Falls back to `MemorySaver` if PostgreSQL is unreachable (dev mode)
- `thread_id = session_id` — each workflow run has its own graph thread

**Crash recovery:** If the orchestrator process dies mid-workflow, the `AsyncPostgresSaver` retains the graph state at the last completed node. On restart, `graph.aget_state(config)` can be called to inspect where execution paused, and `graph.ainvoke(Command(resume=…))` restarts from that exact checkpoint.

---

## 5. Transport Layer — NATS JetStream + HTTP Fallback

### Request-reply pattern

```
Orchestrator                  NATS Server                    KIO Shell (_poll_loop)
     │                             │                               │
     │  1. nc.subscribe(_kio_reply.{corr_id}, cb=_on_reply)      │
     │─────────────────────────────►│                              │
     │                             │                               │
     │  2. js.publish(kio.{id}.request, {…, _reply_to: …})       │
     │─────────────────────────────►│                              │
     │                             │  3. KIO pulls via fetch(1)   │
     │                             │◄─────────────────────────────│
     │                             │                               │ handler()
     │                             │                               │
     │                             │  4. msg.ack()                │
     │                             │◄─────────────────────────────│
     │                             │  5. nc.publish(_kio_reply.X) │
     │                             │◄─────────────────────────────│
     │  6. _on_reply(msg)          │                               │
     │◄─────────────────────────────│                              │
```

**Key properties:**
- Subscribe for reply **before** publishing request (avoids race where reply arrives before sub is set up)
- JetStream for the request path — durable **pull consumer**, `max_deliver=3`, `ack_wait = kio_client_timeout (seconds)`
- KIO shells use `js.pull_subscribe()` + async `_poll_loop()` with `psub.fetch(batch=1, timeout=2.0)`
- Push consumers are NOT compatible with WorkQueue-retention streams in nats-py 2.15+
- Core NATS for the reply path — fast, ephemeral, no persistence needed
- `asyncio.get_running_loop().create_future()` for the reply future — safe in Python 3.12

### Stream configuration

```
Stream:    KIO_JOBS
Subjects:  kio.*.request
Retention: WorkQueue  (message deleted after ack)
Storage:   File  (survives NATS restart)
Max age:   1 hour  (dead messages auto-expired)
```

### HTTP fallback

When `USE_NATS=false` (or NATS is unreachable):
- `KioClient._execute_http()` does `POST /execute` with the same envelope dict
- `kio_base.make_kio_app()` exposes `/execute` on every KIO always
- `KIO_BASE_HOST` determines the hostname: `localhost` locally, KIO service name in Docker

---

## 6. KIO Shell Architecture

Every KIO shell is built by `make_kio_app(kio_id, title, handler)`:

```
make_kio_app()
  │
  ├── GET  /health/                → {"status":"ok","service":"kioN"}
  │
  ├── POST /execute                → always available (HTTP, sync test path)
  │     envelope: MessageEnvelope
  │     → handler(envelope) → result_dict
  │     → wraps in JOB_RESULT envelope
  │
  └── lifespan (USE_NATS=true):
        ├── NATS pull consumer kio.{id}.request
        │     durable: "{kio_id}-worker"
        │     _poll_loop(): fetch(batch=1, timeout=2.0) in tight async loop
        │     → asyncio.create_task(_dispatch(msg))
        │     → handler(envelope) → msg.ack() → publish reply to _reply_to
        │
        ├── CAPABILITY_ANNOUNCEMENT on kio.{id}.capability
        │     Published at startup + every 60s (heartbeat loop)
        │     Payload: {kio_id, host, port, capabilities, timestamp}
        │     Orchestrator subscribes kio.*.capability → AgentRegistry
        │
        └── publish_progress() helper available to handlers
              Publishes TASK_STATUS to kio.{id}.status
              Orchestrator subscribes kio.*.status → TASK_PROGRESS SSE
```

### Handler lifecycle

```python
async def handler(envelope: MessageEnvelope) -> dict:
    # 1. Read inputs from envelope.payload
    # 2. Do work (LLM calls, file ops, shell commands, A2A calls)
    # 3. Return result dict:
    {
        "status":        "DONE" | "REVIEW_REQUIRED",
        "artifact_id":   str,
        "artifact_data": dict,   # stored in PostgreSQL
        "message":       str,
        "hitl_question": str,    # only when REVIEW_REQUIRED
    }
    # 4. On ANY exception → platform catches it → returns REVIEW_REQUIRED
    #    (handler must never raise — always wrap in try/except)
```

### LLM provider singleton per KIO

Each KIO that uses the LLM creates its own provider instance (not shared with the LM Engine — they're separate processes):

```python
_provider = None
_provider_lock = asyncio.Lock()

async def _get_provider():
    global _provider
    if _provider is None:
        async with _provider_lock:         # double-checked lock
            if _provider is None:
                _provider = await create_llm_provider()
    return _provider
```

---

## 7. Session Manager — Persistence Layer

Stateless REST service backed by PostgreSQL. Owns all durable state so the Orchestrator can be restarted without data loss.

### API surface

| Endpoint | Description |
|---|---|
| `POST /sessions/` | Create WorkflowRecord |
| `GET /sessions/{id}` | Fetch session state |
| `PUT /sessions/{id}/status` | Transition state machine |
| `POST /sessions/{id}/artifacts` | Register KIO artifact |
| `GET /sessions/{id}/artifacts` | List all artifacts |
| `POST /sessions/{id}/hitl` | Create HITL checkpoint (HumanApprovalRecord) |
| `PUT /sessions/{id}/hitl/{cp_id}` | Resolve checkpoint (APPROVED/REJECTED) |

### Unit of work

```python
# Every write goes through session_scope()
async with sp.session_scope() as repo:
    workflow = await repo.create_workflow(…)
# → commits on clean exit, rolls back on exception

# Every read goes through read_scope()
async with sp.read_scope() as repo:
    artifacts = await repo.list_artifacts_for_workflow(session_id)
# → no commit; read-only
```

### State machine

```
Session status:
  CREATED → ACTIVE → PENDING_REVIEW → ACTIVE → COMPLETED
                                   → FAILED
             ↓
           FAILED
```

---

## 8. LM Engine — LLM Proxy

A thin FastAPI service that exposes `POST /llm/complete` and delegates to the configured provider.

```
POST /llm/complete
{
  "prompt": "…",
  "system": "…",
  "caller": "orchestrator-planner"
}
→
{
  "content": "…",
  "tokens_in": 123,
  "tokens_out": 456,
  "model": "qwen2.5-coder:3b",
  "latency_ms": 1234.5
}
```

The provider is lazily initialised on the first request (with asyncio.Lock). The orchestrator's `LmEngineClient` calls this endpoint; individual KIO shells instantiate their own provider directly via `create_llm_provider()`.

### Provider selection

```
LLM_PROVIDER env var
  │
  ├── "ollama"   → OllamaProvider  (/api/chat, health_check via /api/tags)
  ├── "openai"   → OpenAIProvider  (openai SDK)
  ├── "claude"   → ClaudeProvider  (anthropic SDK)
  └── "mock"     → MockLLMProvider (instant, deterministic, no network)

All wrapped by:
  ObservedLLMProvider → Langfuse tracing (optional, degrades gracefully)
```

---

## 9. Authentication & Security

### JWT flow

```
POST /auth/login  {username, password}
  → get_user_by_username()
  → bcrypt.verify(password, hashed_password)
  → jwt.encode({sub, user_id, iat, exp}, JWT_SECRET_KEY, HS256)
  → {access_token, expires_in}

All /workflow/* requests:
  → HTTPBearer extracts "Authorization: Bearer <token>"
  → decode_token() → {sub, user_id}
  → UserInfo injected into request handler
```

### SSE special case

Browser `EventSource` cannot set request headers. Solution:

```
GET /workflow/events?token=<jwt>
  → dependencies=[] (removes router-level HTTPBearer check)
  → manual: decode_token(token) in endpoint body
  → 401 on missing / expired / invalid token
```

### Password storage

Passwords hashed with bcrypt via passlib (`CryptContext(schemes=["bcrypt"])`). Never stored in plaintext.

### JWT secret validation

```python
@field_validator("jwt_secret_key", mode="after")
def _reject_default_jwt_secret(cls, value):
    if value == "change-me-in-production" and ENV == "production":
        raise ValueError("JWT_SECRET_KEY must be overridden in production")
```

---

## 10. Persistence Schema

### Core tables (Alembic-managed)

```
workflows
  id (PK, UUID)         ← also used as session_id
  name, project_id, state, owner
  correlation_id, session_id, trace_id
  metadata_ (JSONB)
  started_at, completed_at, created_at

tasks
  id (PK, UUID)
  workflow_id (FK → workflows)
  step_id, agent_role, state, kio_id
  idempotency_key (UNIQUE)
  error_code, error_message, retry_count
  started_at, completed_at

artifacts
  id (PK, UUID)
  workflow_id (FK), task_id (FK), kio_id
  artifact_type, content (JSONB), checksum (SHA-256)
  parent_artifact_id (FK → self — lineage graph)
  created_at

human_approvals
  id (PK, UUID)       ← used as checkpoint_id
  workflow_id (FK), task_id (FK), kio_id
  prompt, metadata_ (JSONB)
  decision, feedback, decided_by, decided_at
  created_at

users
  id (PK, UUID)
  username (UNIQUE), email (UNIQUE, nullable)
  hashed_password, is_active
  created_at

agents          ← KIO capability registry / heartbeats
kio_capabilities
metrics, workflow_spans, reports, messages
```

### LangGraph tables (auto-created by `checkpointer.setup()`)

```
checkpoints         ← graph state snapshots per thread_id + checkpoint_id
checkpoint_blobs    ← serialised node state blobs
checkpoint_writes   ← pending writes (for at-least-once node execution)
```

These tables are not managed by Alembic — they are created and maintained by `langgraph-checkpoint-postgres`.

---

## 11. LLM Integration & Hallucination Recovery

### Parsing pipeline

Every LLM response goes through a multi-strategy repair pipeline before use:

```
raw LLM output (str)
        │
        ▼  extract_json_object()
        │
        ├── 1. Try raw string as-is                  → json.loads()
        ├── 2. Strip markdown fences (```json…```)   → json.loads()
        ├── 3. repair_json_text():
        │       ├── remove trailing commas
        │       ├── replace None/True/False → null/true/false
        │       └── close unclosed { [ brackets
        │   → json.loads()
        └── 4. Find first { … last } substring       → json.loads()

Returns: dict | None  (None if all strategies fail)
```

### LM Engine planning — retry strategy

When the LM Engine plans the KIO sequence:

```
Attempt 1: prompt → extract_json_object → validate KIOs → deduplicate → cap at 8
  │
  ▼ if validation fails
Attempt 2: same prompt (model may produce different output on retry)
  │
  ▼ if still fails
Fallback: ["kio3", "kio5"]  (hardcoded safe default)
```

Validation checks:
- `kio_sequence` is a list
- Each element is in `_VALID_KIOS` (`kio2`–`kio13`)
- No duplicates (order preserved, deduped)
- At most 8 KIOs

### KIO-level degradation

When a KIO's handler catches an exception (including LLM unavailable, malformed response, timeout):

```python
except Exception as exc:
    return {
        "status": "REVIEW_REQUIRED",     # triggers HITL
        "artifact_data": {"error": str(exc), …},
        "hitl_question": "KIO N encountered an error. Continue?",
    }
```

This ensures the workflow never crashes silently — a human always gets to decide what to do next.

---

## 12. Human-in-the-Loop (HITL)

### Trigger conditions

1. **KIO-driven:** handler returns `"status": "REVIEW_REQUIRED"` — the KIO decided review is needed
2. **Orchestrator-driven:** `kio_id in hitl_after` — user requested forced review after this KIO regardless of status

### Execution flow

```
run_kio_node
    │ returns REVIEW_REQUIRED or kio_id in hitl_after
    ▼
hitl_node
    ├── sm.create_hitl_checkpoint()   → HumanApprovalRecord in PG
    ├── update active[session_id]["status"] = "BLOCKED"  ← Slide 19 compliance
    ├── emit SSE WORKFLOW_BLOCKED event
    ├── emit SSE HITL_CHECKPOINT event
    └── interrupt({checkpoint_id, hitl_question, kio})
        ← LangGraph saves state to AsyncPostgresSaver
        ← ainvoke() returns (graph suspended)

... time passes, human reviews dashboard ...

POST /workflow/{session_id}/approve  {feedback: "looks good"}
    ├── sm.resolve_checkpoint()       → HumanApprovalRecord.decision = APPROVED
    ├── emit SSE HITL_APPROVED event
    └── asyncio.create_task(_resume_graph())
            └── graph.ainvoke(Command(resume=feedback))
                → hitl_node receives feedback, returns {"feedback": feedback}
                → advance_node → should_continue → next KIO
```

### Crash safety

If the orchestrator crashes while waiting for HITL approval:
- The graph state is in PostgreSQL (`checkpoints` table)
- The `HumanApprovalRecord` is in the `human_approvals` table
- On restart, `init_runner()` recreates the runner but the `_active` dict is empty
- **Implemented:** `rehydrate()` on startup scans PG for non-terminal sessions (`QUEUED`, `VALIDATING`, `READY`, `RUNNING`, `BLOCKED`, `ACTIVE`, `PENDING_REVIEW`) and re-populates `_active` from the LangGraph checkpoint state — approve() works correctly after a restart

---

## 13. Real-Time Streaming — SSE

### Architecture

```
EventBus (singleton, in-process)
  ├── _queues: list[asyncio.Queue]   ← one per connected SSE client
  └── publish(event)                ← snapshot-safe: iterates list copy

Each SSE client (GET /workflow/events?token=…):
  └── subscribe() generator
        ├── appends Queue to _queues
        ├── starts heartbeat task (puts None sentinel every 15s)
        ├── yields events until client disconnects
        └── finally: cancels heartbeat, removes Queue from _queues

WorkflowEvent.to_sse() → "data: {json}\n\n"
```

### Event types

| Event | Trigger |
|---|---|
| `CONNECTED` | Client connects |
| `HEARTBEAT` | Every 15 seconds (keepalive) |
| `SESSION_CREATED` | `runner.run()` called — status QUEUED |
| `PLANNING_STARTED` / `PLANNING_DONE` | `plan_node` |
| `WORKFLOW_VALIDATING` | `plan_node` starts validation — status VALIDATING |
| `WORKFLOW_READY` | `plan_node` done, kio_sequence set — status READY |
| `WORKFLOW_RUNNING` | `run_kio_node` begins a step — status RUNNING |
| `KIO_STARTED` / `KIO_DONE` | `run_kio_node` dispatches / receives result; KIO_DONE includes `execution_time_ms` |
| `WORKFLOW_BLOCKED` | `hitl_node` about to pause — status BLOCKED |
| `HITL_CHECKPOINT` | `hitl_node` fires `interrupt()` with checkpoint details |
| `HITL_APPROVED` | `runner.approve()` called |
| `TASK_PROGRESS` | Intermediate status from KIO via `publish_progress()` → `kio.*.status` |
| `WORKFLOW_COMPLETED` | `complete_node` |
| `WORKFLOW_FAILED` | `_handle_failure()` or `TimeoutMonitor.cancel()` |

### Dashboard consumption

```javascript
const es = new EventSource(`/api/workflow/events?token=${jwt}`);
es.onmessage = (e) => {
  const event = JSON.parse(e.data);
  // event.event_type, event.session_id, event.message, event.timestamp
};
```

---

## 14. MCP Tools

The platform exposes Model Context Protocol tools both via REST (`/mcp/tools/call`) and as direct Python imports for KIO handlers.

### Registered tools

| Tool | Description | Key args |
|---|---|---|
| `filesystem.read_file` | Read file content (UTF-8) | `path`, `max_chars` (default 8000) |
| `filesystem.list_directory` | List dir entries | `path`, `pattern` (glob), `recursive` |
| `filesystem.write_file` | Write/append file | `path`, `content`, `append` |
| `shell.run_command` | Run shell command | `command`, `cwd`, `timeout` (max 120s) |

### Tool response format

```json
{
  "content": [{"type": "text", "text": "…output…"}]
}
```

### Adding custom tools

```python
from shared.mcp.registry import get_registry

registry = get_registry()
registry.register({
    "name": "myteam.custom_tool",
    "description": "Does X",
    "inputSchema": {
        "type": "object",
        "properties": {"param": {"type": "string"}},
        "required": ["param"]
    }
}, handler=my_async_fn)
```

---

## 15. Agent-to-Agent (A2A) Protocol

A KIO can call another KIO directly without routing through the orchestrator workflow loop. Both share `session_id` and `workflow_id` so all produced artifacts are visible in the same session.

```
KIO5 handler
  │
  └── a2a.invoke("kio12", session_id=…, workflow_id=…, caller_kio="kio5", payload={…})
        │
        ├── USE_NATS=true  → js.request_reply("kio12", envelope)
        │                     (same JetStream stream, same reply pattern as orchestrator)
        │                     Falls back to HTTP if NATS unavailable
        │
        └── USE_NATS=false → HTTP POST http://kio12:8022/execute
                              (KIO_BASE_HOST="" → kio_id used as Docker DNS hostname)
```

### Implemented scenario — kio5 → kio12 OWASP enrichment

kio5 (Bug Detector) runs its own LLM-based detection, then calls kio12 (Cybersecurity)
via A2A with the same code for an independent OWASP Top 10 scan.  The results are
merged into kio5's single artifact — the orchestrator sees one pipeline step.

```
Pipeline view (orchestrator):          Actual execution (inside kio5):
─────────────────────────────          ──────────────────────────────────────
kio1  → route                          kio5 handler()
kio5  → bug detection      ←─────────    ├── LLM: detect bugs (own analysis)
                                          └── A2A invoke("kio12", code=raw_code)
                                                └── kio12 handler()
                                                      └── LLM: OWASP scan
                                          merge: bugs + owasp_vulnerabilities
                                          return combined artifact_data
```

kio5's artifact_data after A2A:

```json
{
  "bugs": [{"severity": "CRITICAL", "kind": "sql_injection", ...}],
  "owasp_vulnerabilities": [{"cwe": "CWE-89", "line": 5, ...}],
  "owasp_summary": "SQL injection on line 5, hardcoded credentials on line 18",
  "hardened_files": [{"path": "inline_code.py", "content": "...fixed code..."}]
}
```

### Transport resolution

```python
# shared/a2a/client.py  — _get_http_client()
host = cfg.kio_base_host or kio_id   # "" → "kio12" (Docker DNS)
base_url = f"http://{host}:{port}"   # "http://kio12:8022"
```

NATS is tried first (durable, at-least-once); on failure the client falls back to
the HTTP `/execute` endpoint that every KIO always exposes.

**When to use A2A:**
- Your KIO needs a sub-result from another KIO as part of its own step
- The sub-call should not appear as a separate pipeline step in the dashboard
- Both KIOs are guaranteed to be running

**When NOT to use A2A:**
- The step should be independently visible and pauseable — put it in `kio_sequence` instead

### Verified test (2026-06-09) — Claude Haiku

Input code: SQLite CRUD with SQL injection + hardcoded token + plaintext password.

```bash
POST /workflow/prompt
{
  "prompt": "bu kodda güvenlik açıklarını bul",
  "code": "import sqlite3\n\ndef get_user(username):\n    cursor.execute(f\"SELECT * FROM users WHERE username = \"{username}\"\")\n    ..."
}

# Container logs confirmed end-to-end A2A:
# [kio5] Direct code mode — 570 chars
# [kio5] A2A → kio12 OWASP scan (session=248dd234)
# [kio12] JOB_REQUEST (HTTP) from kio5          ← caller = kio5, not orchestrator
# [kio12] Using inline code (570 chars)
# [kio5] A2A ← kio12: 8 OWASP finding(s), 3 hardened file(s)
```

Results from single pipeline step (kio5 artifact):

```
kio5 own bugs (6):
  [CRITICAL] sql_injection       — f-string interpolation in get_user() SELECT
  [CRITICAL] sql_injection       — f-string interpolation in delete_user() DELETE
  [CRITICAL] plaintext_password  — user[2] == password (no hashing)
  [HIGH]     hardcoded_secret    — token = "hardcoded-secret-token-123"
  [MEDIUM]   resource_leak       — DB connections never closed
  [MEDIUM]   missing_validation  — no type/bounds check on user_id

kio12 OWASP findings (8) via A2A:
  CWE-89   line=6   SQL Injection — get_user()
  CWE-89   line=12  SQL Injection — delete_user()
  CWE-256  line=17  Plaintext password storage/comparison
  CWE-798  line=18  Hardcoded credentials
  CWE-330  line=18  Insufficient randomness for auth token
  CWE-404  line=4   Unclosed DB connections
  CWE-778  line=1   No audit logging for security events
  CWE-20   line=15  Missing input validation

kio12 hardened files (3):
  inline_code.py   — 8303 chars (parameterised queries, bcrypt, secrets, logging)
  requirements.txt — passlib, bcrypt, secrets
  db_setup.py      — schema with proper constraints
```

**Note:** `kio_client_timeout` raised from 120s → 300s in `shared/config.py` to
accommodate the sequential LLM calls (kio5 ~35s + kio12 ~65s = ~100s total).

---

## 16. Observability

### Logging

All services use **Loguru** with structured log output. Format: `{level} | {time} | {message}`.

```python
from loguru import logger
logger.info("Session {} started — kio_sequence={}", session_id, kio_sequence)
logger.warning("LM planning failed ({}); using fallback", exc)
logger.exception("Workflow {} failed", session_id)  # includes full traceback
```

### Langfuse (optional)

Set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in `.env`. When set, every LLM call is traced:

```
Langfuse trace
  └── workflow span
        ├── plan span (prompt + response)
        ├── kio3 span (prompt + response + token counts + latency)
        └── kio5 span
```

Degrades gracefully — no keys = no tracing, platform continues normally.

### NATS monitoring

```
http://localhost:8222/         NATS server info
http://localhost:8222/jsz      JetStream stats
http://localhost:8222/connz    Active connections
```

### PostgreSQL query inspection

```bash
docker compose exec postgres psql -U enisalimerge -c \
  "SELECT id, state, owner, created_at FROM workflows ORDER BY created_at DESC LIMIT 10;"
```

---

## 17. Docker Deployment

### Service dependency graph

```
postgres ──────────────────────────────────────────────────────────────►
    │                                                                   │
    └─► migrate (alembic upgrade head, restart: "no")                  │
    │                                                                   │
nats ────────────────────────────────────────────────────────────────► │
    │                                                                   │
    └─► session_manager ──────────────────────────────────────────────►│
    └─► lm_engine                                                       │
    └─► orchestrator (depends: nats, session_manager, lm_engine)       │
    └─► kio2 … kio13 (depends: nats, session_manager)                  │
    └─► dashboard (depends: orchestrator)                               │
```

### KIO Dockerfile pattern

All 12 KIO shells use a single image with an `ARG KIO_ID`:

```dockerfile
ARG KIO_ID=kio3
ENV KIO_ID=${KIO_ID}
ENV KIO_PORT=8013

CMD python apps/kio_shells/${KIO_ID}/main.py
```

Each KIO service in docker-compose passes its ID at build time:

```yaml
kio4:
  build:
    context: .
    dockerfile: apps/kio_shells/Dockerfile
    args:
      KIO_ID: kio4
  environment:
    KIO_ID: kio4
    KIO_PORT: 8014
```

### Volume mounts

| Volume | Mount | Purpose |
|---|---|---|
| `postgres-data` | `/var/lib/postgresql/data` | PostgreSQL durability |
| `nats-data` | `/data` | JetStream stream durability |
| `${HOST_REPO_PATH}` | `/repos/target:ro` | Source repo for KIO3/KIO5 analysis |

### Networking

All services on the default bridge network. Container DNS resolution:
- `postgres:5432`, `nats:4222`, `session_manager:8002`, `lm_engine:8001`
- `kio3:8013`, `kio5:8015`, etc. (used when `KIO_BASE_HOST=""`)

---

## 18. Design Decisions & Trade-offs

### LangGraph over a for-loop

| | For-loop | LangGraph |
|---|---|---|
| State persistence | In-memory dict (lost on restart) | PostgreSQL checkpoint (crash-safe) |
| HITL implementation | `asyncio.wait_for(event)` — polling, timeout risk | `interrupt()` / `Command(resume=…)` — no polling, no timeout |
| Branching / retry | Requires explicit `if/else` in runner | Conditional edges — declarative, testable |
| Observability | Manual logging | Built-in graph state inspection via `aget_state()` |

### NATS JetStream over direct HTTP

| | HTTP | NATS JetStream |
|---|---|---|
| KIO restart during execution | Request lost | Re-delivered after `ack_wait` expires |
| Multiple KIO instances | Needs load balancer | WorkQueue retention auto-distributes |
| A2A calls | Works everywhere | Faster, avoids TCP handshake overhead |
| Dev without NATS | Native | `USE_NATS=false` fallback to HTTP |

### PostgreSQL LangGraph checkpointer over Redis

Redis is faster but adds another infrastructure dependency. PostgreSQL already exists for the session/artifact store, so reusing it avoids operational overhead. LangGraph's `AsyncPostgresSaver` is purpose-built for this use case.

### In-process SSE bus over Redis Pub/Sub

The `EventBus` uses asyncio Queues within the orchestrator process. This means:
- SSE only works when connected to the single orchestrator instance
- Zero additional dependencies for events

If horizontal scaling is needed: replace `EventBus` with a Redis Pub/Sub adapter (the `subscribe()` interface stays the same).

### Single Dockerfile for all KIOs

Reduces build complexity from 12 separate Dockerfiles to one. The trade-off is that all KIOs share the same Python dependencies. Teams with highly divergent dependencies should split their Dockerfile.

---

## 19. Prompt Router & Full Platform Diagram

**Added in Phase 9.** Two new entry points replace the need to manually specify `kio_sequence`:

| Endpoint | Use case |
|---|---|
| `POST /workflow/run` | Manual — caller provides `kio_sequence` explicitly |
| `POST /workflow/prompt` | Natural language — kio1 decides the pipeline at runtime |

### kio1 — Prompt Router

kio1 is always the first KIO when using `/workflow/prompt`. It receives the user's
natural-language description (and optional code snippet) and returns a `kio_sequence`
that replaces the graph's sequence via the existing dynamic-pipeline mechanism already
used by kio2.

```
POST /workflow/prompt
{
  "prompt": "bir fastapi uygulaması yaz, delete'te auth olmasın, ve bug bul",
  "code":   null          ← optional; omit to let kio9 generate code
}

kio1 LLM decision examples
─────────────────────────────────────────────────────────────────────
Prompt                               → kio_sequence
─────────────────────────────────────────────────────────────────────
"şu kodda bug bul"    + code         → [kio1, kio5]
"bug bul ve patch yaz" + code        → [kio1, kio5, kio6, kio7]
"kod yaz ve bug bul"  (no code)      → [kio1, kio9, kio5]
"full pipeline çalıştır"             → [kio1, kio3, kio4, kio5, kio6, kio7, kio8]
"analiz et"                          → [kio1, kio3, kio5]
"test yaz"                           → [kio1, kio4]
─────────────────────────────────────────────────────────────────────
Fallback (LLM error)                 → [kio1, kio5]
```

kio1's response also carries `hitl_after` (default: `["kio5"]` whenever kio5 is
in the sequence), which the orchestrator injects into the graph state alongside the
updated `kio_sequence`.

### kio9 — Code Generator

kio9 generates code from a description. If the user requests an intentional bug or
vulnerability, kio9 includes it — the platform is a security-training system where
downstream KIOs (kio5, kio6, kio7) detect and fix the flaw.

kio9's `artifact_data.code` field is read directly by kio5 when no upstream
`findings` list is present (direct-code mode).

### `initial_context` — code injection into the graph

`POST /workflow/prompt` accepts an optional `code` field. The orchestrator stores it
as `initial_context` in `WorkflowGraphState` and passes it in every KIO's payload:

```python
payload = {
    "description":       state["description"],
    "working_directory": state["working_directory"],
    "feedback":          state.get("feedback", ""),
    "last_artifact":     last_artifact,
    "initial_context":   state.get("initial_context", {}),  # ← code lives here
}
```

kio1 reads `payload["initial_context"]["code"]` and places it in its own
`artifact_data.code`, making it available to kio5 as `last_artifact.code`.

### Full platform ASCII diagram

```
╔══════════════════════════════════════════════════════════════════════════════════════╗
║                         KIO1 AI ENGINEERING PLATFORM                               ║
╚══════════════════════════════════════════════════════════════════════════════════════╝

  CLIENT
  ──────
  ┌─────────────────────────────────────────┐
  │  POST /workflow/prompt                  │  ← doğal dil + isteğe bağlı kod
  │  POST /workflow/run                     │  ← manual kio_sequence
  │  POST /workflow/{id}/approve            │  ← HITL onayı
  │  GET  /workflow/{id}/status             │  ← polling
  │  GET  /workflow/events  (SSE)           │  ← live stream
  └──────────────────┬──────────────────────┘
                     │  JWT Auth
╔════════════════════▼══════════════════════════════════════════════════════════════╗
║  ORCHESTRATOR  :8000                                                             ║
║                                                                                  ║
║  ┌─────────────────────────────────────────────────────────────────────────────┐ ║
║  │                     LangGraph StateGraph                                    │ ║
║  │                                                                             │ ║
║  │  START → [plan] → [run_kio] ──should_hitl──► [hitl] ──► [advance]          │ ║
║  │                      ▲                         │            │               │ ║
║  │                      │         interrupt()     │            ▼               │ ║
║  │                      │         suspends here   │     should_continue        │ ║
║  │                      └─────────────────────────┘       ↙         ↘         │ ║
║  │                                                   [run_kio]   [complete]    │ ║
║  │                                                                    │        │ ║
║  │                                                                   END       │ ║
║  └─────────────────────────────────────────────────────────────────────────────┘ ║
║                                                                                  ║
║  WorkflowRunner                    Per-session asyncio.Task                      ║
║  ├── _active{} ← in-memory cache   Session A ──────────────────────────────────► ║
║  ├── _session_locks{} ← race guard Session B ────────────────────────────────►   ║
║  └── rehydrate() ← restart recovery Session C ──── [HITL paused] ─────────────► ║
╚══════════════════════════════════════════════════════════════════════════════════╝
                     │  HTTP
                     │  kio.execute(kio_id, payload)
                     ▼
╔══════════════════════════════════════════════════════════════════════════════════╗
║  KIO POOL  (paylaşılan HTTP microservice'ler — her biri ayrı container)         ║
║                                                                                  ║
║  ┌──────────────────────────────────────────────────────────────────────────┐   ║
║  │  ROUTER & GENERATORS                                                     │   ║
║  │                                                                          │   ║
║  │  ┌─────────────────────────────────┐   ┌────────────────────────────┐   │   ║
║  │  │  kio1  :8011  Prompt Router     │   │  kio9  :8019  Code Gen     │   │   ║
║  │  │                                 │   │                            │   │   ║
║  │  │  prompt → LLM → kio_sequence[]  │   │  description → LLM → code  │   │   ║
║  │  │                                 │   │  (intentional bugs ok)     │   │   ║
║  │  │  "bug bul"    → [kio1,kio5]     │   └────────────────────────────┘   │   ║
║  │  │  "yaz+bul"    → [kio1,kio9,kio5]│                                    │   ║
║  │  │  "patch yaz"  → [kio1,kio5,     │   ┌────────────────────────────┐   │   ║
║  │  │                  kio6,kio7]     │   │  kio2  :8012  Planner      │   │   ║
║  │  │  "full"       → [kio1,kio3..8]  │   │  → returns full pipeline   │   │   ║
║  │  └─────────────────────────────────┘   └────────────────────────────┘   │   ║
║  └──────────────────────────────────────────────────────────────────────────┘   ║
║                                                                                  ║
║  ┌──────────────────────────────────────────────────────────────────────────┐   ║
║  │  ANALYSIS PIPELINE                                                       │   ║
║  │                                                                          │   ║
║  │  kio3 :8013          kio4 :8014          kio5 :8015                      │   ║
║  │  Repo Analyzer   →   Test Generator  →   Bug Detector                   │   ║
║  │  reads disk          pytest files        direct code OR findings         │   ║
║  │                                          → always REVIEW_REQUIRED        │   ║
║  │                                                    ║                     │   ║
║  │                                             ╔══════╩══════╗              │   ║
║  │                                             ║  HITL GATE  ║              │   ║
║  │                                             ║  human must ║              │   ║
║  │                                             ║  approve    ║              │   ║
║  │                                             ╚══════╦══════╝              │   ║
║  │                                                    ║                     │   ║
║  │  kio6 :8016          kio7 :8017          kio8 :8018                      │   ║
║  │  Patch Generator →   Test Re-runner  →   Report Generator               │   ║
║  │  LLM writes fix      pytest runs         final summary                  │   ║
║  └──────────────────────────────────────────────────────────────────────────┘   ║
║                                                                                  ║
║  ┌──────────────────────────────────────────────────────────────────────────┐   ║
║  │  SPECIALIST KIOs (kio10-kio13)  — isteğe bağlı                          │   ║
║  │  kio10: TinyML/Energy  kio11: Test Automation  kio12: Security Scan      │   ║
║  │  kio13: Dev Training                                                     │   ║
║  └──────────────────────────────────────────────────────────────────────────┘   ║
╚══════════════════════════════════════════════════════════════════════════════════╝
                     │                              │
          ┌──────────┘                              └──────────┐
          ▼                                                    ▼
╔═════════════════════════╗                    ╔══════════════════════════╗
║  SESSION MANAGER  :8002 ║                    ║  INFRASTRUCTURE          ║
║                         ║                    ║                          ║
║  sessions table         ║                    ║  PostgreSQL :5432        ║
║  artifacts table        ║                    ║  ├── sessions / artifacts ║
║  hitl_checkpoints table ║                    ║  └── LangGraph checkpts  ║
║                         ║                    ║      (AsyncPostgresSaver) ║
║  REST API:              ║                    ║                          ║
║  POST /sessions/        ║                    ║  NATS :4222              ║
║  PUT  /{id}/status      ║                    ║  └── event bus (SSE)     ║
║  POST /{id}/hitl        ║                    ║                          ║
║  PUT  /{id}/hitl/{ckpt} ║                    ║  LM Engine :8001         ║
╚═════════════════════════╝                    ║  └── plan_workflow()     ║
                                               ╚══════════════════════════╝

  DATA FLOW EXAMPLE — "fastapi uygulaması yaz, delete'te auth olmasın, bug bul"
  ──────────────────────────────────────────────────────────────────────────────

  Client ──POST /workflow/prompt──► Orchestrator
                                         │
                              session oluştur (PostgreSQL)
                                         │
                              asyncio.Task başlat
                                         │
                               ┌─── LangGraph ───┐
                               │   run_kio(kio1) │
                               │        │        │
                               │    kio1 karar   │
                               │ → [kio1,kio9,   │
                               │    kio5,kio6,   │
                               │    kio7]        │
                               │        │        │
                               │   run_kio(kio9) │ ← kod üret
                               │        │        │
                               │   run_kio(kio5) │ ← bug bul  ──► HITL GATE
                               │        │        │                    │
                               │        │        │         human onayı (approve)
                               │        │        │                    │
                               │   run_kio(kio6) │ ← patch yaz ◄─────┘
                               │        │        │
                               │   run_kio(kio7) │ ← test çalıştır
                               │        │        │
                               │   complete      │
                               └─────────────────┘
                                         │
                               artifacts PostgreSQL'de
                               session COMPLETED

  PARALLEL SESSIONS NOTE
  ──────────────────────
  The vertical flow above shows one session's intra-KIO ordering.
  N independent sessions each run as a separate asyncio.Task and may be at
  different pipeline steps simultaneously — all sharing the same KIO container
  pool on a first-come-first-served basis per container.

  To scale throughput: increase KIO replicas in docker-compose
  (e.g. deploy.replicas: 3 on kio5) — each replica handles one request at a time.
```

---

## 20. Orchestrator State Machine

Implemented in `apps/orchestrator/src/engine/orchestrator_state.py`. Singleton: `get_orchestrator_sm()`.

```
                        agent_registered()
                              │
                              ▼
    process start      ┌─────────────┐   workflow_submitted()    ┌────────────┐
       ──────────────► │ INITIALIZING│ ─────────────────────────►│   ACTIVE   │
                       └─────────────┘                           └─────┬──────┘
                              │                                        │
                   workflow_submitted()                    agent_failure_detected()
                   (no agents yet)                                     │
                              │                                        ▼
                              ▼                                  ┌──────────┐
                         ┌────────┐     agent_failure_detected() │ DEGRADED │
                         │  IDLE  │ ─────────────────────────────►│          │
                         │        │ ◄────────────────────────────│          │
                         └───┬────┘     system_restored()        └──────────┘
                             │                                        │
                   workflow_submitted()                    critical_failure()
                             │                                        │
                             ▼                                        ▼
                        ┌────────┐                            ┌──────────┐
                        │ ACTIVE │                            │ RECOVERY │
                        └────────┘                            └──────────┘
                                                                     │
                                                          system_restored() / shutdown()
                                                                     │
                                                                     ▼
                                                              ┌──────────┐
                                                              │ SHUTDOWN │
                                                              └──────────┘
```

| State | `accepts_workflows()` | Meaning |
|---|---|---|
| `INITIALIZING` | false | Process started, no agents announced yet |
| `IDLE` | true | All agents healthy, no workflows running |
| `ACTIVE` | true | At least one workflow in flight |
| `DEGRADED` | true | One or more agents went stale (>120s since last announcement) |
| `RECOVERY` | false | Critical failure mode — workflows blocked until system_restored() |
| `SHUTDOWN` | false | Graceful shutdown initiated |

**GET /status** returns:
```json
{
  "state": "ACTIVE",
  "active_workflows": 2,
  "degraded_agents": [],
  "accepts_workflows": true
}
```

---

## 21. Timeout Monitor

Implemented in `apps/orchestrator/src/engine/timeout_monitor.py`. Singleton: `get_timeout_monitor(active)`.

The `TimeoutMonitor` runs a background `asyncio.Task` that sweeps `_active` every 5 seconds. If a session's `deadline` (ISO UTC string) is in the past, it calls `runner.cancel(session_id, "TASK_TIMEOUT")`.

```python
# How deadline is set
if timeout_seconds:
    deadline = (datetime.now(UTC) + timedelta(seconds=timeout_seconds)).isoformat()
    _active[session_id]["deadline"] = deadline

# How the monitor sweeps
async def _sweep():
    now = datetime.now(UTC)
    for session_id, state in list(self._active.items()):
        deadline_str = state.get("deadline")
        if deadline_str:
            dl = datetime.fromisoformat(deadline_str)
            if now > dl:
                await self._cancel_cb(session_id, "TASK_TIMEOUT")
```

`cancel()` on WorkflowRunner: marks status FAILED, calls `sm.update_status()`, emits `WORKFLOW_FAILED` SSE with `{"cancelled": True}`, notifies OrchestratorStateMachine.

`timeout_seconds` is also forwarded to each KIO in the payload so the KIO can apply its own per-task deadline.

---

## 22. Dynamic Agent Discovery

Implemented in `apps/orchestrator/src/engine/agent_registry.py`. Singleton: `get_agent_registry()`.

```
KIO Shell startup / every 60s
  └── publish to kio.{id}.capability (Core NATS, not JetStream)
        {
          "kio_id":       "kio5",
          "host":         "kio5",           ← Docker DNS name
          "port":         8015,
          "capabilities": ["bug_detection", "owasp_scan"],
          "timestamp":    "2026-06-09T…"
        }

Orchestrator (lifespan)
  └── js.subscribe_core("kio.*.capability", _on_capability)
        └── registry.handle_announcement(data)
              ├── stores (host, port) per kio_id
              ├── timestamps last_seen
              ├── if endpoint changed: calls on_endpoint_change callbacks
              │     → KioClient._invalidate_client(kio_id)
              └── calls get_orchestrator_sm().agent_registered(kio_id)
                              or .agent_recovered(kio_id)

KioClient._get_http_client(kio_id)
  ├── 1. registry.get_endpoint(kio_id)         ← dynamic (announced host:port)
  │         if stale (>120s): logs warning, calls sm.agent_failure_detected()
  └── 2. static fallback: kio_port_map + kio_base_host

GET /agents → registry.list_agents()
  → [{"kio_id", "host", "port", "capabilities", "last_seen", "stale"}]
```

Agent staleness threshold is 120 seconds. A KIO is considered stale if `now - last_seen > 120s`. The `_stale_notified` flag prevents repeated `agent_failure_detected()` calls for the same stale agent — it resets on the next successful announcement.
