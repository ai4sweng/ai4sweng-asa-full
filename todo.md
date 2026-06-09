# EnisAliMerge — Implementation Roadmap

Reference: `orch_interfaces.pptx` (20 slides, AI4SEC, 17.02.2026)  
Last updated: 2026-06-09

---

## Completed Work

### Infrastructure & Transport
- [x] NATS JetStream stream `KIO_JOBS` with WorkQueue retention
- [x] Pull consumer per KIO (nats-py 2.15 compatible, `max_age`/`ack_wait` in seconds)
- [x] Core NATS subscriptions: `kio.*.capability`, `kio.*.status`, `kio.*.heartbeat`
- [x] HTTP fallback when NATS unavailable (USE_NATS=false)
- [x] Docker Compose: all 13 KIOs + orchestrator + session_manager + lm_engine + postgres + nats

### Orchestrator Engine
- [x] LangGraph StateGraph: plan → run_kio → [hitl] → advance → complete
- [x] AsyncPostgresSaver checkpointer (crash-safe graph state)
- [x] MemorySaver fallback when PostgreSQL unavailable
- [x] Per-session asyncio.Lock (prevents double-approve race)
- [x] Session rehydration on restart (restores in-flight sessions from PostgreSQL)
- [x] WorkflowRunner singleton with `run()`, `approve()`, `cancel()`

### Orchestrator State Machine — Slide 16
- [x] States: INITIALIZING → IDLE → ACTIVE ↔ DEGRADED ↔ RECOVERY → SHUTDOWN
- [x] Transitions driven by: agent announcements, workflow start/finish, agent staleness
- [x] `GET /status` endpoint exposes current state + active_workflows + degraded_agents
- [x] `accepts_workflows` flag blocks new submissions in RECOVERY/SHUTDOWN/INITIALIZING

### Workflow State Machine — Slide 19
- [x] QUEUED (on session create)
- [x] VALIDATING (during LM planning)
- [x] READY (after plan, before first KIO)
- [x] RUNNING (during KIO execution)
- [x] DISPATCHED (between RUNNING and KIO reply)
- [x] BLOCKED (during HITL checkpoint) — Slide 19
- [x] PENDING_REVIEW (legacy alias, kept for approve endpoint compat)
- [x] COMPLETED / FAILED

### Message Envelope — Slide 7
- [x] `message_id`, `correlation_id`, `step_id`, `protocol_version` (1.0.0)
- [x] `project_id` (explicit field, not just reused as `source`)
- [x] `session_id`, `workflow_id`, `source`, `target`, `timestamp`, `message_type`
- [x] `payload` dict (Slide 7 `body`)
- [ ] Header/body structural separation (currently flattened) — cosmetic, low priority

### Task Request Body — Slide 8
- [x] `retry_policy` object (`max_retries`, `backoff_strategy`) sent in payload
- [x] `timeout_seconds` per-task (forwarded in payload, used by kio_base handler)
- [ ] `task_id` explicit field in payload (currently uses session_id)
- [ ] `priority` field
- [ ] `input_artifacts[]` array (currently uses `last_artifact` dict)
- [ ] `expected_outputs[]` array

### Task Response — Slide 10 / 11
- [x] `execution_time_ms` in KIO_DONE SSE event
- [x] `artifact_id`, `artifact_data` (stored in PostgreSQL via Session Manager)
- [x] Structured error: `{"error_code", "error_message", "retryable"}` — Slide 11
- [ ] `logs_reference` field (e.g. `logstore://session_id`)
- [ ] Artifact `location` (S3 URL) — requires Phase 5
- [ ] Artifact `checksum` (sha256) — requires Phase 5
- [ ] `task_id` in response payload

### Agent API — Slide 13
- [x] `onTaskRequest` → `/execute` POST + NATS pull consumer
- [x] `publishTaskResponse` → JOB_RESULT envelope
- [x] `publishHeartbeat` → `_heartbeat_loop()` every 30s on `kio.{id}.heartbeat`
- [x] `publishCapability` → `_capability_loop()` every 60s on `kio.{id}.capability`
- [x] `publish_progress()` helper function in kio_base (optional, Slide 9)
- [ ] `publishTaskStatus` used in real KIO handlers (kio2–kio13 don't call it yet)
- [ ] ACKNOWLEDGED state: KIO publishes receipt before processing

### Orchestrator API — Slide 14
- [x] `registerAgent` → `agent_registry.handle_announcement()`
- [x] `dispatchTask` → `kio.execute()` from graph_nodes
- [x] `updateTaskState` → `sm.update_progress()` in session_client
- [x] `finalizeTask` → implicit in run_kio_node result handling
- [x] `handleFailure` → `_handle_failure()` in workflow_runner

### Orchestrator Internal Modules — Slide 17
- [x] Workflow Manager → `workflow_runner.py`, `workflow_graph.py`
- [x] Agent Registry → `agent_registry.py`
- [x] State Store → PostgreSQL via Session Manager
- [x] Event Processor → `event_bus.py`
- [x] Timeout Monitor → `timeout_monitor.py` (sweeps every 5s, auto-cancel on deadline)
- [ ] Task Scheduler (explicit module — currently nodes dispatch inline)
- [ ] Provenance Manager (formal module — currently lineage stored in JSON content)
- [ ] Retry Manager (formal module — currently only LLM fallback retry exists)
- [ ] Compensation Engine — requires Phase 5

### Capability Announcement — Slide 12
- [x] `agent_id`, `version`, `supported_tasks`, `hardware_requirements`, `endpoint`
- [x] Re-announced every 60s
- [x] Orchestrator AgentRegistry updated on each announcement
- [x] `GET /agents` lists all known agents with liveness status
- [ ] `version` hardcoded to "0.1.0" — should come from pyproject.toml/env
- [ ] `hardware_requirements` hardcoded — should be configurable per KIO

### Authentication & Security
- [x] JWT Bearer tokens (HS256)
- [x] JWT_SECRET_KEY validation: min 32 chars, rejects "change-me-in-production"
- [x] Per-user SSE filtering (owner field on WorkflowEvent)
- [x] Rate limiting: 20 concurrent active sessions per user
- [x] Ownership checks on /status and /approve endpoints
- [x] UUID v4 validation on workflow_id and session_id path params

### Provenance & Artifact Lineage
- [x] `parent_artifact_id` stored in artifact content JSON
- [x] Artifact registered in Session Manager after each KIO step
- [x] `correlation_id` ties all envelopes of the same workflow together
- [ ] Provenance Manager: formal query API (e.g. `GET /artifacts/{id}/lineage`)
- [ ] FK-based lineage (currently JSON content — FK constraint mismatch with KIO UUIDs)

### KIO Implementations
- [x] kio1 — Prompt Router (real: routes to correct pipeline based on description)
- [x] kio5 — Bug Detector (real: LLM-based static analysis, OWASP checks, HITL)
- [x] kio4 — Test Generator (real: LLM-based)
- [x] kio6 — Patch Agent (real: LLM-based)
- [x] kio7 — Test Re-run Agent (real: LLM-based)
- [x] kio8 — Evidence Report Agent (real: LLM-based)
- [x] kio9 — Code Generator (real: LLM-based)
- [x] kio12 — AI Cybersecurity / A2A OWASP (real: LLM-based)
- [x] kio2 — Planning Agent (real: LLM selects optimal KIO sequence from description)
- [x] kio3 — Repo Analyzer (real: LLM-based per-file analysis with RepoContextBuilder)
- [x] kio10 — TinyML / Energy Efficiency (real: LLM quantisation/pruning recommendations)
- [x] kio11 — AI Test Automation Tool (real: LLM-based pytest suite generation)
- [x] kio13 — Developer Training (real: LLM generates modules, exercises, quiz from pipeline artifacts)

### DevOps & Observability
- [x] Loguru structured logging across all services
- [x] `GET /health/` on every service
- [x] Docker healthchecks (postgres, nats, session_manager, lm_engine)
- [x] Configurable checkpointer pool size (`CHECKPOINTER_POOL_SIZE`)
- [x] JWT_SECRET_KEY + JWT_EXPIRE_MINUTES forwarded to all KIO containers via x-common-env

---

## What Needs to Be Done — Incremental Phases

---

### Phase 3 — Task State Machine Completion ✅ COMPLETE (2026-06-09)

#### 3.1 — ACKNOWLEDGED State ✅
- `kio_base._make_nats_handler`: publishes `kio.{id}.status` with `status=ACKNOWLEDGED` before calling handler
- `orchestrator/main._on_task_status`: handles ACKNOWLEDGED, emits `TASK_ACKNOWLEDGED` SSE

#### 3.2 — TIMEOUT State ✅
- `workflow_runner.cancel("TASK_TIMEOUT")`: sets TIMEOUT → WORKFLOW_TIMEOUT SSE → FAILED
- `timeout_monitor._sweep`: skips TIMEOUT and CANCELLED sessions

#### 3.3 — CANCELLED State + Endpoint ✅
- `POST /workflow/{session_id}/cancel` added to router
- `CancelResponse` schema added
- `workflow_runner.cancel("CANCELLED")`: sets CANCELLED, emits `WORKFLOW_CANCELLED` SSE

#### 3.4 — RETRYING State + Real Retry ✅
- `run_kio_node`: retry loop using `RetryManager.should_retry()` + backoff
- Emits `TASK_RETRYING` SSE with attempt/max_retries counts
- Resets counter on success, cleans up on completion

#### 3.5 — publish_progress in Real KIO Handlers ✅
- kio4, kio5, kio6, kio7, kio8, kio9, kio12 all call `publish_progress()` at ~10/40/75/90%

#### 3.6 — Retry Manager Module ✅
- `src/engine/retry_manager.py`: RetryManager with exponential backoff, per-session state

#### 3.7 — logs_reference Field ✅
- `kio_base.py` injects `"logs_reference": f"logstore://{session_id}/{step_id}"` into every JOB_RESULT (HTTP + NATS)

---

### Phase 4 — Provenance Manager + Task Scheduler ✅ COMPLETE (2026-06-09)

#### 4.1 — Provenance Manager ✅
- `src/engine/provenance_manager.py`: `get_lineage(session_id, artifact_id)` walks parent chain; `get_full_lineage(session_id)` returns all artifacts in order
- `src/api/provenance_router.py`: `GET /workflow/{session_id}/artifacts` and `GET /workflow/{session_id}/artifacts/{artifact_id}/lineage`
- Registered in `orchestrator/main.py`

#### 4.2 — Task Scheduler ✅
- `src/engine/task_scheduler.py`: `schedule(kio_sequence, current_step, registry) → kio_id | None`
- `run_kio_node` calls scheduler instead of inline `kio_seq[step]`
- Also provides `find_capable_agent(task_type, registry)` for future rerouting

#### 4.3 — Capability-Based Task Matching ✅
- Scheduler checks `is_alive(kio_id)` and `supported_tasks` match
- Emits `TASK_NO_CAPABLE_AGENT` SSE; raises RuntimeError (feeds RetryManager)
- Safe fallback: if registry is empty (HTTP mode / NATS not yet connected), skips check

#### 4.4 — ID Unification ✅ (no migration needed)
- `repositories.create_artifact`: accepts optional `artifact_id` param used as DB PK
- `session_service.register_artifact`: passes KIO artifact_id as DB PK
- `session_service.get_artifacts`: returns `parent_artifact_id` from ORM FK column + `created_at`
- `session_service.get_artifact(artifact_id)`: new single-artifact lookup
- `session_service._to_session_dict`: handles TIMEOUT, CANCELLED, BLOCKED states
- `session_service.update_status`: maps TIMEOUT, CANCELLED, BLOCKED to WorkflowState
- `constants.WorkflowState`: added TIMEOUT and CANCELLED values
- Session Manager router: new `GET /sessions/{session_id}/artifacts/{artifact_id}` endpoint
- No DB migration needed — existing auto-generated PKs untouched; new rows use KIO UUIDs

---

### Phase 5 — S3 Artifact Storage (Breaking Change)

**Goal:** Slide 10 `location` and `checksum` fields; move artifact_data from PostgreSQL JSON to S3.

#### 5.1 — S3 Client Module
- **Where:** new `shared/storage/s3_client.py`
- **What:** Async wrapper around `aioboto3`; `upload_artifact(session_id, artifact_id, data)` → S3 URL; `download_artifact(url)` → dict
- **Config:** `S3_BUCKET`, `S3_ENDPOINT_URL` (for MinIO local dev), `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` in `shared/config.py`
- **Files:** new `shared/storage/s3_client.py`, `shared/config.py`, `docker-compose.yml` (add MinIO service)

#### 5.2 — MinIO for Local Development
- **Where:** `docker-compose.yml`
- **What:** Add `minio` service (image: `minio/minio`) with persistent volume; add `mc` init container to create bucket
- **Config:** `S3_ENDPOINT_URL=http://minio:9000`, `S3_BUCKET=enisalimerge-artifacts`
- **Files:** `docker-compose.yml`

#### 5.3 — KIO Handler Output Contract Change (Breaking)
- **Current:** handlers return `{"artifact_data": {...large dict...}}`
- **New:** handlers return `{"artifact_data": {...}, "artifact_location": "s3://...", "artifact_checksum": "sha256:..."}`
- **What kio_base does:** after handler returns, upload `artifact_data` to S3, compute checksum, attach `location` and `checksum` to JOB_RESULT
- **Affected:** ALL KIOs (kio1–kio13 handlers)
- **Files:** `kio_base.py` (upload in `/execute` after handler), all KIO `main.py` files
- **Risk:** HIGH — every KIO must be rebuilt and redeployed atomically
- **Migration strategy:** Feature flag `USE_S3=true/false`; when false, skip upload and leave location empty (backwards compat during rollout)

#### 5.4 — Session Manager Schema Change
- **Current:** `artifacts` table stores full `artifact_data` in `content` JSON column (potentially MBs per row)
- **New:** `content` stores only metadata + S3 URL; `artifact_data` no longer stored in DB
- **What changes:** `session_service.py` stores `{"s3_location": url, "checksum": hash, "stage": ..., "parent_artifact_id": ...}` instead of full data
- **Migration:** DB migration to drop content size limit or clear existing artifact_data; existing rows get `s3_location=null`
- **Files:** `session_manager/src/service/session_service.py`, migration script
- **Risk:** HIGH — data migration; existing artifact_data in DB becomes inaccessible until downloaded from S3

#### 5.5 — Artifact Retrieval API
- **Where:** Session Manager
- **What:** `GET /sessions/{id}/artifacts/{artifact_id}/download` → streams S3 object or returns presigned URL
- **Files:** `session_manager/src/api/router.py`, `session_manager/src/service/session_service.py`
- **Risk:** Low — new endpoint, additive

#### 5.6 — Checksum Verification
- **Where:** `kio_base.py` and Session Manager
- **What:** Before uploading, compute `sha256(json.dumps(artifact_data))`; store in JOB_RESULT and in Session Manager; verify on download
- **Files:** `kio_base.py`, `shared/storage/s3_client.py`

---

### Phase 6 — Compensation Engine (Requires Phase 5)

**Goal:** Slide 17/18 Compensation Engine; COMPENSATING and COMPENSATED states.

#### 6.1 — Compensation Engine Module
- **Where:** new `src/engine/compensation_engine.py`
- **What:** On workflow FAILED, walks completed steps in reverse and calls each KIO's compensation endpoint (`DELETE /artifacts/{id}` or S3 delete)
- **States added:** COMPENSATING → COMPENSATED
- **Interface:** `compensate(session_id, completed_steps)` async coroutine
- **Files:** new `src/engine/compensation_engine.py`, `graph_nodes.py` (trigger on failure)
- **Risk:** High — KIOs need compensation endpoints; S3 deletes are irreversible

#### 6.2 — KIO Compensation Endpoints
- **Where:** `kio_base.py`, each KIO
- **What:** `DELETE /artifacts/{artifact_id}` endpoint on each KIO; removes produced outputs (S3 delete, DB cleanup)
- **Files:** `kio_base.py` (add to make_kio_app), all KIO `main.py` compensation handlers
- **Risk:** High — destructive, must be idempotent

#### 6.3 — COMPENSATING / COMPENSATED SSE Events
- **Where:** `compensation_engine.py`, `event_bus.py`
- **What:** Emit `WORKFLOW_COMPENSATING`, `STEP_COMPENSATED` (per step), `WORKFLOW_COMPENSATED` events
- **Files:** `compensation_engine.py`

---

### Phase 7 — Remaining KIO Implementations ✅ COMPLETE (2026-06-10)

| KIO | Title | Status |
|-----|-------|--------|
| kio2 | Planning Agent | ✅ LLM selects optimal pipeline; returns `kio_sequence` + `hitl_after` |
| kio3 | Repo Analyzer | ✅ RepoContextBuilder + per-file LLM analysis; Ollama→Anthropic fallback |
| kio10 | TinyML / Energy Efficiency | ✅ LLM quantisation/pruning/distillation recommendations |
| kio11 | AI Test Automation | ✅ LLM pytest suite + conftest + hypothesis generation |
| kio13 | Developer Training | ✅ LLM modules, exercises, quiz from upstream pipeline artifacts |

---

### Phase 8 — GitHub Repository & CI/CD ✅ COMPLETE (2026-06-10)

#### 8.1 — Git Init & Remote ✅
- Repo already on `https://github.com/ai4sweng/ai4sweng-asa-full`
- `.gitignore` updated: covers `.env`, logs/, `__pycache__`, `.venv/`, volumes

#### 8.2 — GitHub Actions CI ✅ `.github/workflows/ci.yml`
- **lint**: `ruff check` + `ruff format --check` on push/PR
- **typecheck**: `mypy` on shared/ and orchestrator/src/ (advisory, continue-on-error)
- **test**: DB migrations + `pytest tests/` with postgres + nats service containers
- **build**: `docker build` smoke test for orchestrator, session_manager, lm_engine, kio_shells (matrix)
- Concurrency group cancels in-progress runs on new push

#### 8.3 — Release Workflow ✅ `.github/workflows/release.yml`
- Triggers on `v*.*.*` tags
- Builds and pushes all 15 images (3 core + 13 KIOs) to `ghcr.io/ai4sweng/<name>:<version>`
- Multi-arch: `linux/amd64` + `linux/arm64`
- GHA layer cache (`type=gha`) shared across builds
- Creates GitHub Release with auto-generated changelog from git log

#### 8.4 — Dependabot ✅ `.github/dependabot.yml`
- Weekly updates for: GitHub Actions, shared pip, orchestrator pip, session_manager pip, kio_shells pip, dashboard npm

---

### Phase 9 — Test Suite

**Goal:** Automated verification of the platform.

#### 9.1 — Orchestrator Unit Tests
- `test_orchestrator_state.py`: all SM transitions
- `test_timeout_monitor.py`: deadline detection
- `test_retry_manager.py`: backoff calculation (after Phase 3.4)
- `test_agent_registry.py`: stale detection, endpoint resolution

#### 9.2 — Integration Tests
- `test_pipeline_sql_injection.py`: kio1 → kio5 full flow
- `test_hitl_approve.py`: HITL checkpoint + approve + resume
- `test_nats_transport.py`: JetStream publish/pull consumer round-trip
- `test_timeout.py`: session cancelled on deadline

#### 9.3 — KIO Handler Tests (per real KIO)
- Mock envelope fixtures
- Assert output schema matches spec (artifact_id, artifact_data keys)
- Test error path (LLM timeout → retryable=true error)

---

## Quick Reference: Status by Slide

| Slide | Topic | Status |
|-------|-------|--------|
| 7 | Message Envelope | ✅ Complete (flattened, project_id added) |
| 8 | Task Request Body | ⚠️ Partial (retry_policy sent, timeout_seconds used; task_id/priority/arrays missing) |
| 9 | Task Status Messages | ✅ Complete (publish_progress wired into kio4/5/6/7/8/9/12; ACKNOWLEDGED via NATS) |
| 10 | Task Response (Success) | ⚠️ Partial (execution_time_ms + logs_reference done; S3 location/checksum missing) |
| 11 | Task Response (Failure) | ✅ Complete |
| 12 | Capability Registration | ✅ Complete |
| 13 | Agent API Interface | ⚠️ Partial (publishTaskStatus not wired in handlers) |
| 14 | Orchestrator API Interface | ✅ Complete (informal interface) |
| 16 | Orchestrator State Machine | ✅ Complete |
| 17 | Orchestrator Internal Modules | ⚠️ Partial (RetryManager ✅ Task Scheduler ✅ Provenance Mgr ✅; Compensation Engine missing) |
| 18 | Task State Machine | ⚠️ Partial (ACKNOWLEDGED ✅ RETRYING ✅ TIMEOUT ✅ CANCELLED ✅; COMPENSATING missing) |
| 19 | Workflow Transitions | ✅ Complete |

---

## Risk Classification

| Phase | Risk | Reason |
|-------|------|--------|
| Phase 3 | 🟢 Low | Additive states and modules, no schema changes |
| Phase 4 | 🟡 Medium | Task Scheduler changes dispatch path; ID unification needs DB migration |
| Phase 5 | 🔴 High | All KIO handlers rewritten; DB schema change; irreversible S3 operations |
| Phase 6 | 🔴 High | Destructive compensation; KIOs need new endpoints; depends on Phase 5 |
| Phase 7 | 🟡 Medium | Per-KIO LLM prompt engineering; no infrastructure changes |
| Phase 8 | 🟢 Low | Additive CI/CD; no production impact |
| Phase 9 | 🟢 Low | Test-only, no production changes |
