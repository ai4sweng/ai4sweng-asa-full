# EnisAliMerge — TODO

**Last updated:** 2026-06-10  
**Test baseline:** 269 passed · 0 failed · 4 skipped (NATS) (see `TEST_REPORT.md`)

---

## Status Summary

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Infrastructure & transport (NATS, HTTP, Docker) | ✅ Done |
| 2 | Core orchestrator (LangGraph, SM, provenance) | ✅ Done |
| 3 | Task state machine (ACKNOWLEDGED, TIMEOUT, RETRYING, CANCELLED) | ✅ Done |
| 4 | Provenance manager + task scheduler + ID unification | ✅ Done |
| 5 | S3 artifact storage (MinIO, aiobotocore, s3_key column) | ✅ Done |
| 6 | Compensation engine (COMPENSATING/COMPENSATED, KIO DELETE) | ✅ Done |
| 7 | All 13 KIO real implementations | ✅ Done |
| 8 | GitHub Actions CI/CD + release workflow + Dependabot | ✅ Done |
| 9.1 | Unit tests — orchestrator engine modules | ✅ Done |
| 9.2 | Integration tests — pipeline + HITL flow | ✅ Done |
| 9.3 | Integration tests — timeout, NATS, KIO handlers | ✅ Done |
| 10 | Payload spec gaps (task_id, priority, input_artifacts[]) | ✅ Done |
| 11 | Observability (presigned URLs, checksum, publishTaskStatus) | ⬜ Not started |
| 12 | Session Manager HTTP integration tests | ✅ Done |

---

## Phase 9.3 — Remaining Tests

### 9.3.1 — Timeout and Cancel Tests

**File:** `tests/test_timeout.py`

- `test_cancel_transitions_to_cancelled` — `runner.cancel(session_id)` → `CANCELLED` in SM + `WORKFLOW_CANCELLED` SSE
- `test_cancel_task_timeout_transitions_to_failed` — `runner.cancel(session_id, reason="TASK_TIMEOUT")` → `TIMEOUT` then `FAILED`
- `test_get_state_returns_none_after_cancel` — `_active` cleared after cancel
- `test_timeout_monitor_cancels_overdue_session` — mock `TimeoutMonitor._sweep` with overdue deadline; verify cancel called

**Why:** `WorkflowRunner.cancel()` is a public API used by the timeout monitor and the cancel endpoint. No test currently covers it.

---

### 9.3.2 — WorkflowRunner Rehydrate Tests

**File:** `tests/test_workflow_runner_rehydrate.py`

- `test_rehydrate_restores_pending_review_session` — mock SM with one PENDING_REVIEW session + graph checkpoint; verify `_active` repopulated
- `test_rehydrate_skips_terminal_sessions` — COMPLETED/FAILED sessions not added to `_active`
- `test_rehydrate_tolerates_missing_checkpoint` — session in SM but no graph checkpoint: skipped gracefully
- `test_rehydrate_empty_session_list` — no sessions: returns without error

**Why:** `rehydrate()` is called at startup to restore in-flight sessions. Failures here leave HITL sessions permanently stuck after an orchestrator restart.

---

### 9.3.3 — KIO Handler Tests

**File:** `tests/test_kio_handlers.py`

One fixture class per real KIO. Each test builds a `MessageEnvelope` and calls the handler directly (no HTTP, no NATS).

| KIO | Tests |
|-----|-------|
| kio2 (Planner) | Returns `kio_sequence` list; `hitl_after` list; status `DONE` |
| kio3 (Repo Analyzer) | Returns `artifact_data` with expected keys; handles empty repo gracefully |
| kio5 (Bug Detector) | Returns `REVIEW_REQUIRED` when bugs found; returns `DONE` on clean input |
| kio8 (Evidence Report) | Output contains summary key; `artifact_type` is correct |
| Error path (any KIO) | LLM timeout → `retryable: true` in error payload |

**Technique:** patch the LLM client (`lm.agenerate` or `lm.ainvoke`) with `AsyncMock` returning a canned response. No real LLM calls needed.

**Why:** Handlers can silently break if the LLM response schema changes. Unit tests catch contract mismatches before deployment.

---

### 9.3.4 — NATS Transport Tests

**File:** `tests/test_nats_transport.py`

Requires a real NATS server — skip with `@pytest.mark.skipif(not os.getenv("USE_NATS"), reason="NATS not configured")`.

- `test_jetstream_publish_and_pull` — publish to `kio.kio2.request`; pull consumer receives it; ACK
- `test_capability_announcement_received` — KIO publishes `CAPABILITY_ANNOUNCEMENT`; orchestrator's `handle_announcement` invoked
- `test_heartbeat_received_within_interval` — KIO publishes `HEARTBEAT`; orchestrator receives within 35s

**Why:** HTTP transport is tested via integration tests. NATS path is untested — a bug in the JetStream consumer wiring would only surface in production.

---

### 9.3.5 — Provenance Manager Tests

**File:** `tests/test_provenance_manager.py`

- `test_get_lineage_single_artifact` — one artifact, no parent: returns single-item list
- `test_get_lineage_chain` — artifact C → B → A: returns [A, B, C] in order
- `test_get_lineage_handles_missing_artifact` — artifact not found: returns empty list
- `test_get_full_lineage_all_artifacts` — all artifacts for session returned

**Why:** `ProvenanceManager` is wired into the API but has no dedicated test. Lineage correctness is critical for audit.

---

## Phase 10 — Payload Spec Gaps (Slides 8 / 10 / 11)

Low-risk, additive changes. No infrastructure impact.

### 10.1 — `task_id` Field in Payload

**Current:** `task_id` absent from KIO payloads; orchestrator re-uses `session_id`.  
**Required:** Slide 8 specifies an explicit `task_id` separate from `session_id`.

- Add `task_id` to payload dict as `f"{session_id}:step_{n}_{kio_id}"`
- Echo in JOB_RESULT envelope
- Return in `TASK_RETRYING` and `TASK_NO_CAPABLE_AGENT` SSE events

**Files:** `shared/contracts/`, `apps/kio_shells/kio_base.py`, `apps/orchestrator/src/engine/graph_nodes.py`

---

### 10.2 — `priority` Field

**Current:** Not implemented.  
**Required:** Slide 8 specifies `priority` (integer 1–10) in task request.

- Add `priority: int = 5` to `TaskRequest` Pydantic model
- Pass from `run_kio_node` payload
- `TaskScheduler` uses priority for tie-breaking when multiple capable agents exist

**Files:** `shared/contracts/`, `apps/orchestrator/src/engine/graph_nodes.py`, `apps/orchestrator/src/engine/task_scheduler.py`

---

### 10.3 — `input_artifacts[]` Array

**Current:** `last_artifact` dict (only the most recent step's output).  
**Required:** Slide 8 specifies `input_artifacts[]` as a typed array of artifact references.

- Replace `last_artifact` key with `input_artifacts: list[dict]`
- Each entry: `{"artifact_id": str, "artifact_type": str, "s3_key": str | None}`
- `run_kio_node` builds the list from `state["artifacts"]` by fetching metadata from SM
- All KIO handlers updated to consume `input_artifacts[0]` instead of `last_artifact`

**Files:** `apps/orchestrator/src/engine/graph_nodes.py`, `apps/kio_shells/kio_base.py`, all KIO `main.py` handlers  
**Risk:** Medium — all KIO handlers reference `last_artifact`; needs coordinated update

---

### 10.4 — `expected_outputs[]` Array

**Current:** Not implemented.  
**Required:** Slide 8 specifies `expected_outputs[]` describing what the KIO should produce.

- Add `expected_outputs: list[str]` field to payload (e.g. `["bug_report", "patch_diff"]`)
- KIO handlers optionally validate output type against this list

**Files:** `shared/contracts/`, `apps/orchestrator/src/engine/graph_nodes.py`

---

## Phase 11 — Observability and Contract Completeness

### 11.1 — Artifact Presigned URL in API Response

**Current:** `GET /sessions/{id}/artifacts` returns `s3_key` but no presigned URL.  
**Required:** Slide 10 specifies `location` field with a usable download URL.

- `session_service.get_artifacts()` calls `store.presigned_url(s3_key)` when `s3_key` is set
- Add `location: str | None` to artifact response dict
- New endpoint: `GET /sessions/{id}/artifacts/{artifact_id}/download` → redirect to presigned URL

**Files:** `apps/session_manager/src/service/session_service.py`, `apps/session_manager/src/api/router.py`

---

### 11.2 — Artifact Checksum (sha256)

**Current:** No checksum stored or verified.  
**Required:** Slide 10 specifies `checksum` field.

- In `session_service.register_artifact()`: compute `sha256(json.dumps(artifact_data, sort_keys=True))`
- Store in artifact record (new `ArtifactRecord.checksum` column or in `content`)
- Return in `get_artifact()` / `get_artifacts()` response

**Files:** `apps/session_manager/src/service/session_service.py`, `shared/persistence/models.py`, new migration `0004_add_artifact_checksum.py`

---

### 11.3 — `publishTaskStatus` in Real KIO Handlers

**Current:** `ACKNOWLEDGED` published by `kio_base` on NATS path only. Handlers themselves do not publish `RUNNING` or `COMPLETED` status.  
**Required:** Slide 13 — handlers call `publishTaskStatus` at key lifecycle points.

- Publish `RUNNING` status at handler entry
- Publish `COMPLETED` status before handler returns (supplements the JOB_RESULT)

**Files:** each KIO `main.py` handler (kio2–kio13)

---

### 11.4 — Agent Version from pyproject.toml

**Current:** `version: "0.1.0"` hardcoded in capability announcement.  
**Fix:** Read from `KIO_VERSION` env var (injected at build time).

**Files:** `apps/kio_shells/kio_base.py`, `docker-compose.yml`, `.github/workflows/release.yml`

---

## Phase 12 — Session Manager API Integration Tests

**File:** `tests/test_session_manager_api.py`

Uses `httpx.AsyncClient` with `ASGITransport` against the real FastAPI app in-process. Mocks the `SessionService` singleton.

| Test | Endpoint | Assertion |
|------|----------|-----------|
| `test_create_session` | `POST /sessions/` | 201; body contains `session_id` |
| `test_get_session_not_found` | `GET /sessions/{id}` | 404 for unknown ID |
| `test_update_status_valid` | `PUT /sessions/{id}/status` | 200; SM `update_status` called |
| `test_register_artifact` | `POST /sessions/{id}/artifacts` | 201; body contains `artifact_id` |
| `test_get_artifacts` | `GET /sessions/{id}/artifacts` | 200; list returned |
| `test_create_hitl_checkpoint` | `POST /sessions/{id}/hitl` | 201; body contains `checkpoint_id` |
| `test_resolve_checkpoint_approved` | `PUT /sessions/{id}/hitl/{ckpt}/resolve` | 200; `action=APPROVED` |
| `test_resolve_checkpoint_rejected` | same endpoint | 200; `action=REJECTED` |
| `test_list_sessions` | `GET /sessions/` | 200; array in body |

---

## Deferred / Low Priority

These are spec gaps that do not block current use cases.

| Item | Slide | Notes |
|------|-------|-------|
| Header/body structural separation in envelope | 7 | Currently flattened — cosmetic only |
| `hardware_requirements` configurable per KIO | 12 | Hardcoded `{"gpu": false, "memory_gb": 2}` |
| FK-based artifact lineage (vs JSON content) | — | Re-keying existing artifact rows is risky |
| Multi-instance KIO pools (load-balancing via `find_capable_agent`) | 17 | Requires pool strategy |
| Dashboard WebSocket live updates | — | Currently SSE-only |
| Structured log shipping to Loki/Elastic | — | `logstore://` reference exists, no backend |
