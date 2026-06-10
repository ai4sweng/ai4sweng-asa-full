# Test Report — EnisAliMerge / KIO1 Platform

**Date:** 2026-06-10  
**Python:** 3.12.13  
**Test runner:** pytest 9.0.3 + pytest-asyncio 1.4.0  
**Total:** 182 passed · 0 failed · 0 skipped  
**Duration:** ~1.9 s

---

## Summary by File

| File | Tests | Type | Component |
|------|------:|------|-----------|
| `test_retry_manager.py` | 28 | Unit | `RetryManager` — backoff, session state |
| `test_orchestrator_state.py` | 24 | Unit | `OrchestratorStateMachine` — all transitions |
| `test_contracts.py` | 20 | Unit | Pydantic contracts — envelopes, artifacts, capabilities |
| `test_agent_registry.py` | 16 | Unit | `AgentRegistry` — announcements, stale detection |
| `test_compensation_engine.py` | 15 | Unit | `CompensationEngine` — S3/KIO delete, order, events |
| `test_task_scheduler.py` | 14 | Unit | `TaskScheduler` — capability matching, routing |
| `test_artifact_store.py` | 13 | Unit | `ArtifactStore` — S3/MinIO put/get/delete |
| `test_persistence_models.py` | 12 | Unit | SQLAlchemy ORM model column definitions |
| `test_capability_registry.py` | 10 | Unit | `CapabilityRegistry` — register, list, planner ctx |
| `test_pipeline_integration.py` | 9 | **Integration** | `WorkflowRunner` end-to-end, LangGraph pipeline |
| `test_hitl_approve.py` | 8 | **Integration** | HITL checkpoint + `approve()` resume flow |
| `test_config.py` | 7 | Unit | `Settings` — validators, defaults, JWT rules |
| `test_session_provider.py` | 6 | Unit | `SessionProvider` — scopes, commit, rollback |

---

## Unit Tests (165 tests)

### `test_orchestrator_state.py` — 24 tests

Tests the `OrchestratorStateMachine` (Slide 16). No mocking — pure Python state machine.

| Test | Assertion |
|------|-----------|
| `test_initial_state_is_initializing` | Starts in INITIALIZING |
| `test_initializing_does_not_accept_workflows` | `accepts_workflows` is False in INITIALIZING |
| `test_first_agent_registered_transitions_to_idle` | INITIALIZING → IDLE on first announcement |
| `test_accepts_workflows_after_idle` | `accepts_workflows` is True in IDLE |
| `test_workflow_submitted_from_idle_transitions_to_active` | IDLE → ACTIVE on `workflow_submitted()` |
| `test_active_accepts_workflows` | Concurrent workflows accepted in ACTIVE |
| `test_all_workflows_done_transitions_to_idle` | ACTIVE → IDLE when counter reaches zero |
| `test_partial_workflow_finish_stays_active` | Stays ACTIVE while other workflows in progress |
| `test_workflow_counter_never_goes_negative` | Guard against under-decrement |
| `test_agent_failure_from_active_transitions_to_degraded` | ACTIVE → DEGRADED on `agent_failed()` |
| `test_degraded_still_accepts_workflows` | `accepts_workflows` True in DEGRADED |
| `test_single_agent_recovery_from_degraded_goes_active` | DEGRADED → ACTIVE when degraded set clears |
| `test_multiple_failures_need_all_recovered` | Stays DEGRADED until all agents recovered |
| `test_recovery_goes_idle_when_no_workflows` | RECOVERY → IDLE on `system_restored()` |
| `test_critical_failure_enters_recovery` | ACTIVE → RECOVERY on `critical_failure()` |
| `test_recovery_rejects_workflows` | `accepts_workflows` False in RECOVERY |
| `test_system_restored_from_recovery_goes_idle` | RECOVERY → IDLE on restore |
| `test_system_restored_from_recovery_goes_active_when_workflows_pending` | RECOVERY → ACTIVE if pending count > 0 |
| `test_shutdown_is_terminal` | SHUTDOWN cannot transition to any state |
| `test_shutdown_not_overridden_by_agent_registration` | Agent announcements ignored in SHUTDOWN |
| `test_state_change_callback_fires` | Callback invoked on every transition |
| `test_callback_exception_does_not_crash_sm` | Exception in callback does not abort SM |
| `test_summary_contains_required_keys` | `summary()` dict has `state`, `active_workflows`, etc. |
| `test_summary_degraded_agents_sorted` | `degraded_agents` list is sorted alphabetically |

---

### `test_retry_manager.py` — 28 tests

Tests `RetryManager` per-session retry state and backoff math.

| Group | Tests | Key assertions |
|-------|------:|----------------|
| `register` | 4 | Creates entry, updates policy without resetting counter, defaults |
| `should_retry` | 3 | True when attempts < max, False when equal, False for unknown |
| `_backoff` (exponential) | 5 | Attempt 1→1.0s, 2→2.0s, 3→4.0s, 4→8.0s, 7→60.0s (cap) |
| `_backoff` (linear) | 4 | Attempt 1→1.0s, 2→2.0s, 5→5.0s, 61→60.0s (cap) |
| `_backoff` (unknown strategy) | 1 | Falls back to exponential |
| `wait_and_increment` | 3 | Returns new count, sleeps correct duration (mocked), zero on unknown |
| `sequential` | 1 | Accumulates attempts across calls |
| `reset` | 2 | Clears counter; noop on unknown session |
| `cleanup` | 2 | Removes session; noop on unknown |
| `get_attempts` | 2 | Zero for unknown; tracks correctly |
| `exhaustion gate` | 1 | After max retries, `should_retry` returns False |

---

### `test_agent_registry.py` — 16 tests

Tests `AgentRegistry` announcement handling, stale detection, and SM integration.

| Test | Assertion |
|------|-----------|
| `test_handle_announcement_registers_agent` | Agent stored after announcement |
| `test_handle_announcement_ignores_missing_agent_id` | Malformed message silently dropped |
| `test_handle_announcement_fires_endpoint_change_cb` | Callback fires on first announcement |
| `test_reannouncement_same_endpoint_does_not_fire_change_cb` | No spurious callback on same endpoint |
| `test_endpoint_change_fires_on_port_change` | Callback fires when port changes |
| `test_get_endpoint_returns_host_port_for_fresh_agent` | Returns `(host, port)` tuple |
| `test_get_endpoint_returns_none_for_unknown_agent` | Unknown agent → None |
| `test_get_endpoint_returns_none_for_stale_agent` | Stale agent → None |
| `test_get_endpoint_notifies_sm_on_first_stale` | SM notified once on first stale detection |
| `test_stale_flag_not_fired_twice` | SM not notified again on subsequent checks |
| `test_is_alive_true_for_fresh_agent` | Fresh agent → True |
| `test_is_alive_false_for_stale_agent` | Stale agent → False |
| `test_list_agents_returns_all_agents` | All registered agents returned |
| `test_list_agents_sorted_by_kio_id` | Alphabetical sort by kio_id |
| `test_list_agents_marks_stale_as_not_alive` | `alive: false` for stale in list |
| `test_concurrent_announcements_do_not_corrupt_state` | 50 concurrent async announcements — no corruption |

---

### `test_compensation_engine.py` — 15 tests

Tests `CompensationEngine` (Phase 6). Uses `reset_compensation_engine()` autouse fixture.

| Test | Assertion |
|------|-----------|
| `test_compensate_empty_list_does_nothing` | No calls on empty artifact list |
| `test_compensate_sets_compensating_then_compensated` | Both states appear in `update_status` calls |
| `test_compensating_status_set_before_compensated` | Strict ordering: COMPENSATING before COMPENSATED |
| `test_workflow_compensating_event_emitted` | `WORKFLOW_COMPENSATING`, `STEP_COMPENSATED`, `WORKFLOW_COMPENSATED` emitted |
| `test_step_compensated_per_artifact` | One `STEP_COMPENSATED` event per artifact |
| `test_compensation_walks_artifacts_in_reverse` | Steps compensated in reverse order |
| `test_s3_delete_called_when_s3_key_present` | `store.delete(s3_key)` called when key set |
| `test_s3_delete_skipped_when_no_s3_key` | No S3 call when `s3_key` is None |
| `test_kio_delete_called_for_known_producer` | HTTP DELETE to `/artifacts/{id}` on KIO |
| `test_kio_delete_404_treated_as_ok` | 404 from KIO is not an error — `WORKFLOW_COMPENSATED` still emitted |
| `test_kio_delete_network_error_is_best_effort` | Network error does not abort compensation |
| `test_status_update_failure_does_not_abort_compensation` | DB failure non-fatal |
| `test_unknown_artifact_id_handled_gracefully` | Missing artifact metadata silently skipped |
| `test_get_compensation_engine_requires_args_on_first_call` | RuntimeError before init |
| `test_get_compensation_engine_returns_same_instance` | Singleton pattern |

---

### `test_task_scheduler.py` — 14 tests

Tests `TaskScheduler` capability-based routing (Slide 17).

| Test | Assertion |
|------|-----------|
| `test_empty_registry_skips_capability_check` | Returns kio_id directly when no agents registered |
| `test_empty_registry_any_step` | Same fallback at any step index |
| `test_step_out_of_range_returns_none` | Out-of-bounds step → None |
| `test_alive_capable_agent_dispatched` | Live agent with matching task_type returned |
| `test_alive_empty_supported_tasks_is_capable` | Empty supported_tasks = universally capable |
| `test_stale_agent_returns_none` | Stale agent → None (even if registered) |
| `test_unregistered_kio_in_non_empty_registry_returns_none` | Unknown KIO in populated registry → None |
| `test_alive_but_wrong_task_type_returns_none` | task_type mismatch → None |
| `test_correct_task_type_among_multiple_passes` | Correct task_type among several entries → passes |
| `test_schedule_second_step` | Step index 1 routes correctly |
| `test_find_capable_agent_returns_matching_agent` | Finds first alive agent for task_type |
| `test_find_capable_agent_skips_dead_agents` | Dead agents excluded |
| `test_find_capable_agent_returns_none_when_no_match` | No match → None |
| `test_find_capable_agent_empty_registry` | Empty registry → None |

---

### `test_artifact_store.py` — 13 tests

Tests `ArtifactStore` S3/MinIO wrapper (Phase 5). Uses `aiobotocore` async context manager mocking.

| Test | Assertion |
|------|-----------|
| `test_enabled_false_when_s3_disabled` | `store.enabled` False when `USE_S3=false` |
| `test_enabled_true_when_s3_enabled` | `store.enabled` True when `USE_S3=true` |
| `test_key_format` | Key is `artifacts/{workflow_id}/{artifact_id}.json` |
| `test_ensure_bucket_noop_when_disabled` | No S3 call when disabled |
| `test_ensure_bucket_creates_when_missing` | `create_bucket` called on 404 |
| `test_ensure_bucket_skips_when_existing` | No create when bucket exists |
| `test_put_uploads_json_and_returns_key` | `put_object` called; returns correct key |
| `test_put_propagates_s3_error` | S3 error propagated to caller |
| `test_get_downloads_and_parses_json` | `get_object` called; body parsed as JSON |
| `test_delete_calls_s3_delete_object` | `delete_object` called with correct key |
| `test_delete_swallows_s3_errors` | S3 error on delete silently ignored |
| `test_presigned_url_returns_url` | `generate_presigned_url` called; URL returned |
| `test_get_artifact_store_returns_same_instance` | Singleton pattern |

---

### `test_persistence_models.py` — 12 tests

Tests SQLAlchemy ORM column definitions (no DB connection required — introspects `__table__`).

| Group | Tests |
|-------|------:|
| `WorkflowRecord` — column presence, nullability | 2 |
| `TaskRecord` — `kio_id` column and nullability | 2 |
| `AgentRecord` — `kio_id` uniqueness, `available`/`port` columns | 3 |
| `ArtifactRecord` — `kio_id` column | 1 |
| `HumanApprovalRecord` — `feedback` and `kio_id` | 1 |
| `KIOCapabilityRecord` — `kio_id` unique, required columns | 2 |
| `MetricRecord` — `kio_id` column | 1 |

---

### `test_capability_registry.py` — 10 tests

Tests `CapabilityRegistry` backed by mock `SessionProvider` with async context manager.

| Group | Tests | Key assertions |
|-------|------:|----------------|
| Register | 2 | Calls `upsert_kio_capability`; no raise for available=True/False |
| List | 3 | Returns `KIOCapability` Pydantic models; `available_only` param delegated; empty case |
| Mark unavailable | 2 | Sets `available=False`; missing KIO silently skipped |
| Planner context | 3 | Empty context is empty; kio_ids present; description included |

---

### `test_config.py` — 7 tests

Tests `Settings` (pydantic-settings) field validators and defaults.

| Test | Assertion |
|------|-----------|
| `test_default_values` | `nats_url`, `jwt_algorithm`, `llm_provider`, pool defaults |
| `test_target_repo_path_normalizes_empty` | Whitespace → default path |
| `test_target_repo_path_normalizes_none` | None → default path |
| `test_jwt_secret_rejected_in_production` | `ENV=production` + default key → `ValidationError` |
| `test_jwt_secret_accepted_when_valid` | Valid 32-char key accepted |
| `test_custom_pool_config` | `db_pool_size` and `db_max_overflow` overridden |
| `test_openai_and_anthropic_keys` | API keys stored as-is |

---

### `test_session_provider.py` — 6 tests

Tests `SessionProvider` scoped transaction management.

| Test | Assertion |
|------|-----------|
| `test_instantiation_with_factory` | Accepts `async_session_factory` |
| `test_session_scope_yields_repository` | `async with session_scope()` yields `Repository` |
| `test_session_scope_commits_on_success` | `session.commit()` called on clean exit |
| `test_session_scope_rolls_back_on_error` | `session.rollback()` called on exception |
| `test_read_scope_yields_repository` | `async with read_scope()` yields `Repository` |
| `test_read_scope_does_not_commit` | `session.commit()` not called in read-only scope |

---

### `test_contracts.py` — 20 tests

Tests shared Pydantic contracts (envelopes, artifacts, capabilities, tasks, errors).

| Group | Tests |
|-------|------:|
| `KIOEnvelope` — build, custom idempotency key, serialization, alias, required fields | 5 |
| `Artifacts` — defaults for 3 types, union membership, `isinstance` check, `created_at` auto-fill | 6 |
| `KIOCapability` — defaults, required `kio_id`, port + tags | 3 |
| `Tasks` — `TaskRequest`/`TaskResult` required fields, missing correlation_id error | 3 |
| `Errors` — `KIOError` attributes, `CompensationError` inheritance, error code constants | 3 |

---

## Integration Tests (17 tests)

### `test_pipeline_integration.py` — 9 tests

Full LangGraph pipeline execution with `MemorySaver` checkpointer and `AsyncMock` service clients.  
No infrastructure (PostgreSQL, NATS, S3) required.

**Setup:** `WorkflowRunner` + `build_workflow_graph(MemorySaver())` + sequential KIO mock with `asyncio.sleep(0)` yield.  
**Terminal detection:** polls `sm.update_status` call log until `COMPLETED` or `FAILED` appears.

| Test | Scenario | Key assertion |
|------|----------|---------------|
| `test_two_step_pipeline_completes` | kio2 → kio8 | `update_status(..., "COMPLETED")` called |
| `test_three_step_pipeline_calls_all_kios` | kio2 → kio3 → kio8 | `kio.execute.call_count == 3` |
| `test_single_step_pipeline_completes` | kio8 only | Completes without error |
| `test_pipeline_registers_artifacts_per_step` | kio2 → kio3 | `sm.register_artifact.call_count == 2` |
| `test_run_creates_session_with_owner` | kio2 | `create_session(owner="alice")` awaited |
| `test_update_status_completed_called` | kio2 | `update_status` receives `"COMPLETED"` |
| `test_kio_exception_transitions_to_failed` | kio2 raises (retries exhausted, no LLM fallback) | Status `FAILED` or `COMPENSATED` |
| `test_get_state_returns_none_for_unknown_session` | — | `get_state("x")` returns None |
| `test_get_state_returns_status_immediately_after_run` | kio2 | State dict available before graph completes |

**Notable techniques:**
- `RetryManager.wait_and_increment` patched on singleton instance to bypass the 1-second backoff
- `graph_nodes.get_settings` patched to suppress `LLM_PROVIDER_FALLBACK` env var so retry exhaustion raises instead of entering HITL

---

### `test_hitl_approve.py` — 8 tests

HITL checkpoint + `approve()` resume flow using LangGraph's `interrupt()` / `Command(resume=...)`.

**Setup:** Same as pipeline tests. `hitl_after=["kio2"]` routes the graph to `hitl_node` after step 1.  
**HITL detection:** polls `runner._active[session_id]["status"]` for `BLOCKED` or `PENDING_REVIEW`.

| Test | Scenario | Key assertion |
|------|----------|---------------|
| `test_hitl_pauses_then_resumes_to_completed` | kio2 → HITL → approve → kio8 | Status `COMPLETED` after approve |
| `test_hitl_sets_pending_checkpoint_id` | kio2 → HITL pause | `active["pending_checkpoint_id"]` is set |
| `test_hitl_approve_calls_resolve_checkpoint` | kio2 → approve | `sm.resolve_checkpoint` awaited with `session_id` |
| `test_hitl_both_kios_execute` | kio2 → HITL → kio8 | `kio.execute.call_count == 2` |
| `test_double_approve_returns_none` | approve twice | Second call returns `None` |
| `test_approve_unknown_session_returns_none` | approve non-existent | Returns `None` |
| `test_hitl_after_last_kio_completes` | kio2 → kio8 → HITL → approve | `COMPLETED` after final step HITL |
| `test_no_hitl_completes_without_approve` | `hitl_after=[]` | Graph finishes without any `approve()` call |

---

## Coverage Notes

### What is covered
- All orchestrator engine state machines and scheduling logic (unit)
- Full end-to-end pipeline graph execution path (integration)
- HITL interrupt, resume, and idempotency guards (integration)
- S3/MinIO artifact upload/download/delete paths (unit, mocked)
- Compensation engine: ordering, best-effort, S3 + HTTP KIO delete (unit)
- Pydantic contracts, Settings validators, ORM models (unit)
- Retry backoff math and session state tracking (unit)

### What is not yet covered
- `WorkflowRunner.cancel()` and `TimeoutMonitor` sweep (no test_timeout.py yet)
- NATS JetStream transport round-trip (no test_nats_transport.py)
- Real KIO handler output validation (no test_kio_handlers.py)
- Session Manager API endpoints (no HTTP-level integration tests)
- Provenance Manager `get_lineage` (no test_provenance.py)
- DB migration scripts (no migration test)
- `WorkflowRunner.rehydrate()` post-restart restore (no test)
