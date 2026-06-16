"""LangGraph node factories for the KIO workflow graph.

Each node is a coroutine that receives the full WorkflowGraphState and returns
a partial dict with only the keys it wants to update.  Service clients and the
event bus are injected via closures built by ``make_nodes()``.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from loguru import logger

from .event_bus import EventBus, WorkflowEvent
from .graph_state import WorkflowGraphState
from shared.config import get_settings


def make_nodes(sm, kio, lm, bus: EventBus, active: dict) -> dict:
    """Return a dict of named node coroutines capturing service clients.

    Parameters
    ----------
    sm:     SessionClient
    kio:    KioClient
    lm:     LmEngineClient
    bus:    EventBus for SSE notifications
    active: shared mutable dict ``session_id → runtime state`` (for status API)
    """

    def _emit(event_type: str, session_id: str, message: str, data: dict | None = None) -> None:
        bus.publish(WorkflowEvent(event_type, session_id, message, data))
        logger.info("[{}] {}", event_type, message)

    # ------------------------------------------------------------------
    # plan
    # ------------------------------------------------------------------

    async def plan_node(state: WorkflowGraphState) -> dict[str, Any]:
        """Plan the KIO sequence (via LM Engine if none supplied), then run
        process reflection — a precondition check that repairs a wrong plan
        (e.g. patching before bugs are confirmed) before it reaches dispatch."""
        from .process_reflection import validate_and_repair_plan

        session_id = state["session_id"]
        has_repo = bool(state.get("working_directory"))
        has_code = bool(state.get("initial_context", {}).get("code"))

        def _reflect_and_ready(
            seq: list[str], reasoning: str = "", confidence_low: bool = False
        ) -> dict[str, Any]:
            """Validate/repair the proposed plan, emit events, mark it READY."""
            result = validate_and_repair_plan(seq, has_repo=has_repo, has_code=has_code)
            # Never empty the plan on a failed repair — fall back to the proposal.
            final = result.sequence or seq
            if result.changed and result.sequence:
                _emit(
                    "PLAN_REFLECTION",
                    session_id,
                    "Plan adjusted by process reflection: " + "; ".join(result.notes),
                    {"before": seq, "after": final, "notes": result.notes},
                )
            if session_id in active:
                active[session_id]["kio_sequence"] = final
                active[session_id]["total"] = len(final)
                active[session_id]["status"] = "READY"
            _emit(
                "WORKFLOW_READY",
                session_id,
                f"Pipeline ready: {' → '.join(k.upper() for k in final)}",
                {"kio_sequence": final, "reasoning": reasoning},
            )
            return {"kio_sequence": final, "plan_confidence_low": confidence_low}

        if state.get("kio_sequence"):
            # Pipeline explicitly provided (manual selection) — trust the caller's
            # choice; reflection only guards the LLM planner's guess below.
            if session_id in active:
                active[session_id]["status"] = "READY"
            _emit(
                "WORKFLOW_READY",
                session_id,
                f"Pipeline ready: {' → '.join(k.upper() for k in state['kio_sequence'])}",
                {"kio_sequence": state["kio_sequence"]},
            )
            return {"plan_confidence_low": False}

        # Need LM planning — transition to VALIDATING (Slide 19)
        if session_id in active:
            active[session_id]["status"] = "VALIDATING"
        _emit(
            "WORKFLOW_VALIDATING",
            session_id,
            "Validating workflow description and planning pipeline…",
        )
        kio_seq, reasoning, used_fallback = await lm.plan_workflow(
            state["description"], session_id
        )
        return _reflect_and_ready(kio_seq, reasoning, confidence_low=used_fallback)

    # ------------------------------------------------------------------
    # run_kio
    # ------------------------------------------------------------------

    async def run_kio_node(state: WorkflowGraphState) -> dict[str, Any]:
        """Dispatch the current KIO shell, register its artifact, and return the result."""
        session_id = state["session_id"]
        workflow_id = state["workflow_id"]
        step = state["current_step"]
        kio_seq = state["kio_sequence"]

        # 4.2 / 4.3: TaskScheduler decides which KIO to dispatch (capability check)
        from .task_scheduler import get_task_scheduler
        from .agent_registry import get_agent_registry

        scheduler = get_task_scheduler()
        registry = get_agent_registry()
        kio_id = scheduler.schedule(kio_seq, step, registry)
        if kio_id is None:
            target_kio = kio_seq[step] if step < len(kio_seq) else "unknown"
            _emit(
                "TASK_NO_CAPABLE_AGENT",
                session_id,
                f"No capable agent available for {target_kio.upper()} (step {step + 1})",
                {"kio": target_kio, "step": step + 1, "status": "NO_CAPABLE_AGENT"},
            )
            raise RuntimeError(
                f"No capable agent for step {step + 1} ({target_kio}): "
                "agent stale or capability mismatch — will retry if configured"
            )

        _emit(
            "KIO_STARTED",
            session_id,
            f"Initiating {kio_id.upper()}… ({step + 1}/{len(kio_seq)})",
            {"kio": kio_id, "step": step + 1, "total": len(kio_seq)},
        )
        if session_id in active:
            active[session_id]["active_kio"] = kio_id
            active[session_id]["progress"] = step
            active[session_id]["status"] = "RUNNING"
        _emit(
            "WORKFLOW_RUNNING",
            session_id,
            f"Executing step {step + 1}/{len(kio_seq)} — {kio_id.upper()}",
            {"kio": kio_id, "step": step + 1},
        )

        last_artifact = state.get("last_result", {}).get("payload", {}).get("artifact_data", {})
        # Phase 10.3: typed artifact references for all upstream outputs
        input_artifacts = [
            {"artifact_id": aid, "artifact_type": "json"} for aid in state.get("artifacts", [])
        ]
        step_id = f"step_{step + 1}_{kio_id}"
        correlation_id = state.get("correlation_id", "")
        parent_artifact_id = state["artifacts"][-1] if state.get("artifacts") else None

        # Emit DISPATCHED before the blocking execute call
        if session_id in active:
            active[session_id]["status"] = "DISPATCHED"
        _emit(
            "KIO_DISPATCHED",
            session_id,
            f"[{kio_id.upper()}] Dispatching job… (step {step + 1}/{len(kio_seq)})",
            {"kio": kio_id, "step": step + 1, "step_id": step_id, "correlation_id": correlation_id},
        )

        retry_policy = {"max_retries": 1, "backoff_strategy": "exponential"}
        from .retry_manager import get_retry_manager

        rm = get_retry_manager()
        rm.register(session_id, retry_policy)

        _t0 = time.monotonic()
        result = None
        while True:
            try:
                result = await kio.execute(
                    kio_id=kio_id,
                    session_id=session_id,
                    workflow_id=workflow_id,
                    payload={
                        "description": state["description"],
                        "working_directory": state["working_directory"],
                        "feedback": state.get("feedback", ""),
                        "last_artifact": last_artifact,  # backward compat
                        "input_artifacts": input_artifacts,  # Phase 10.3: typed refs
                        "initial_context": state.get("initial_context", {}),
                        "llm_provider_override": state.get("llm_provider_override", ""),
                        "retry_policy": retry_policy,
                        "timeout_seconds": state.get("timeout_seconds"),  # per-task (Slide 8)
                        "step_id": step_id,
                        "task_id": step_id,  # Phase 10.1: explicit task_id
                        "priority": state.get("priority", 5),  # Phase 10.2: dispatch priority
                        "expected_outputs": ["artifact"],  # Phase 10.4
                    },
                    correlation_id=correlation_id,
                    step_id=step_id,
                )
                rm.reset(session_id)  # success — reset retry counter for next step
                break  # exit retry loop on success
            except Exception as exc:
                cfg = get_settings()

                # Check if the error payload says retryable=true
                getattr(exc, "retryable", False)
                # Also check if it's a plain exception we should retry (not HITL path)
                already_retried = bool(state.get("llm_provider_override"))

                if rm.should_retry(session_id) and not already_retried:
                    attempt = await rm.wait_and_increment(session_id)
                    if session_id in active:
                        active[session_id]["status"] = "RETRYING"
                    _emit(
                        "TASK_RETRYING",
                        session_id,
                        f"[{kio_id.upper()}] Retrying (attempt {attempt}/{retry_policy['max_retries']})…",
                        {
                            "kio": kio_id,
                            "attempt": attempt,
                            "max_retries": retry_policy["max_retries"],
                            "error": str(exc),
                        },
                    )
                    logger.warning(
                        "[{}] {} failed (attempt {}), retrying: {}",
                        session_id[:8],
                        kio_id,
                        attempt,
                        exc,
                    )
                    if session_id in active:
                        active[session_id]["status"] = "RUNNING"
                    continue  # retry

                # No more retries — offer HITL fallback if configured
                if cfg.llm_provider_fallback and not already_retried:
                    logger.warning(
                        "[{}] {} failed with primary LLM ({}); offering HITL fallback to {}",
                        session_id[:8],
                        kio_id,
                        exc,
                        cfg.llm_provider_fallback,
                    )
                    _emit(
                        "KIO_FAILED",
                        session_id,
                        f"[{kio_id.upper()}] failed — offering switch to {cfg.llm_provider_fallback}",
                        {"kio": kio_id, "error": str(exc)},
                    )
                    return {
                        "last_result": {
                            "payload": {
                                "status": "REVIEW_REQUIRED",
                                "message": str(exc),
                                "artifact_data": {},
                                "hitl_question": (
                                    f"{kio_id.upper()} failed with {cfg.llm_provider!r}. "
                                    f"Approve retry with {cfg.llm_provider_fallback!r}?"
                                ),
                            }
                        },
                        "error": str(exc),
                        "llm_retry_pending": True,
                        "artifacts": [],
                        "feedback": "",
                    }
                raise

        # Job returned — back to ACTIVE while we register the artifact
        execution_time_ms = int((time.monotonic() - _t0) * 1000)
        if session_id in active:
            active[session_id]["status"] = "ACTIVE"

        resp = result.get("payload", {})
        artifact_id = resp.get("artifact_id", str(uuid.uuid4()))
        artifact_data = resp.get("artifact_data", {})
        kio_message = resp.get("message", "Done.")

        try:
            await sm.register_artifact(
                session_id,
                {
                    "artifact_id": artifact_id,
                    "producer_kio": kio_id,
                    "workflow_stage": f"step_{step + 1}_{kio_id}",
                    "artifact_type": "json",
                    "artifact_data": artifact_data,
                    "state": "CREATED",
                    "parent_artifact_id": parent_artifact_id,
                },
            )
        except Exception as reg_exc:
            # Non-fatal: the workflow continues, but the artifact may be missing
            # from Session Manager. Log as error so operators can investigate.
            logger.error(
                "[{}] Failed to register artifact for {} ({}); workflow continues",
                session_id[:8],
                kio_id,
                reg_exc,
            )

        # If this KIO returned a dynamic pipeline, update the sequence in state.
        # kio1 (Router) and kio2 (Planner) use this to tell the orchestrator which KIOs to run.
        new_seq = resp.get("kio_sequence")
        if new_seq and isinstance(new_seq, list) and len(new_seq) > 1:
            new_hitl = resp.get("hitl_after")
            logger.info("[{}] Pipeline updated by KIO: {} hitl_after={}", kio_id, new_seq, new_hitl)
            if session_id in active:
                active[session_id]["kio_sequence"] = new_seq
                active[session_id]["total"] = len(new_seq)
            await sm.update_progress(
                session_id,
                {
                    "completed_kios": step + 1,
                    "total_kios": len(new_seq),
                    "last_kio": kio_id,
                },
            )
            _emit(
                "KIO_DONE",
                session_id,
                f"[{kio_id.upper()}] {kio_message}",
                {
                    "kio": kio_id,
                    "artifact_id": artifact_id,
                    "step": step + 1,
                    "execution_time_ms": execution_time_ms,
                    "tokens_in": resp.get("tokens_in", 0),
                    "tokens_out": resp.get("tokens_out", 0),
                    "kio_sequence": new_seq,
                },
            )
            update: dict = {
                "last_result": result,
                "artifacts": [artifact_id],
                "feedback": "",
                "kio_sequence": new_seq,
            }
            if new_hitl is not None:
                update["hitl_after"] = new_hitl
            return update

        await sm.update_progress(
            session_id,
            {
                "completed_kios": step + 1,
                "total_kios": len(kio_seq),
                "last_kio": kio_id,
            },
        )
        _emit(
            "KIO_DONE",
            session_id,
            f"[{kio_id.upper()}] {kio_message}",
            {
                "kio": kio_id,
                "artifact_id": artifact_id,
                "step": step + 1,
                "execution_time_ms": execution_time_ms,
                "tokens_in": resp.get("tokens_in", 0),
                "tokens_out": resp.get("tokens_out", 0),
            },
        )

        update: dict = {"last_result": result, "artifacts": [artifact_id], "feedback": ""}

        # Data reflection — is this result adequate for the downstream plan?
        # If not (e.g. kio5 confirmed no bugs), prune the now-pointless steps so
        # the pipeline doesn't run a patch/re-test on empty input.
        from .data_reflection import reflect_on_result

        downstream = kio_seq[step + 1 :]
        dr = reflect_on_result(kio_id, artifact_data, downstream)
        if dr.pruned:
            new_seq = kio_seq[: step + 1] + dr.remaining
            if session_id in active:
                active[session_id]["kio_sequence"] = new_seq
                active[session_id]["total"] = len(new_seq)
            update["kio_sequence"] = new_seq
            _emit(
                "DATA_REFLECTION",
                session_id,
                "Plan pruned by data reflection: " + "; ".join(dr.notes),
                {
                    "after_step": step + 1,
                    "pruned": dr.pruned,
                    "kio_sequence": new_seq,
                    "notes": dr.notes,
                },
            )

        # Draft reflection — does this step signal an unresolved outcome
        # (e.g. kio7 reports failing tests)? Recorded for the completion gate.
        from .draft_reflection import assess_step

        outcome = assess_step(kio_id, artifact_data)
        if outcome is not None:
            update["outcome_status"] = outcome
            if outcome != "OK":
                _emit(
                    "DRAFT_REFLECTION",
                    session_id,
                    f"Draft reflection: {kio_id.upper()} reports an unresolved outcome ({outcome})",
                    {
                        "kio": kio_id,
                        "outcome": outcome,
                        "passed": artifact_data.get("passed"),
                        "failed": artifact_data.get("failed"),
                    },
                )

        return update

    # ------------------------------------------------------------------
    # hitl
    # ------------------------------------------------------------------

    async def hitl_node(state: WorkflowGraphState) -> dict[str, Any]:
        """Create a HITL checkpoint in Session Manager then pause via interrupt().

        Execution resumes when the orchestrator's approve() method calls
        graph.ainvoke(Command(resume=feedback), config=thread_config).
        """
        from langgraph.types import interrupt  # local import keeps startup fast

        session_id = state["session_id"]
        step = state["current_step"]
        kio_id = state["kio_sequence"][step]
        artifact_id = state["artifacts"][-1] if state.get("artifacts") else str(uuid.uuid4())
        hitl_q = (
            state.get("last_result", {})
            .get("payload", {})
            .get("hitl_question", f"Review {kio_id.upper()} output before continuing?")
        )

        checkpoint = await sm.create_hitl_checkpoint(
            session_id, step=f"post_{kio_id}", artifact_id=artifact_id
        )
        checkpoint_id: str = checkpoint["checkpoint_id"]

        if session_id in active:
            active[session_id]["status"] = "BLOCKED"  # Slide 19: BLOCKED state
            active[session_id]["pending_checkpoint_id"] = checkpoint_id

        _emit(
            "WORKFLOW_BLOCKED",
            session_id,
            "Workflow blocked — awaiting human review",
            {"kio": kio_id, "checkpoint_id": checkpoint_id},
        )
        _emit(
            "HITL_CHECKPOINT",
            session_id,
            f"[HITL] {hitl_q}",
            {
                "kio": kio_id,
                "checkpoint_id": checkpoint_id,
                "artifact_id": artifact_id,
                "hitl_question": hitl_q,
            },
        )

        # Pause — LangGraph saves state to MemorySaver; runner resumes via Command(resume=…)
        feedback = interrupt(
            {
                "checkpoint_id": checkpoint_id,
                "hitl_question": hitl_q,
                "kio": kio_id,
            }
        )
        return {"feedback": feedback or "", "pending_checkpoint_id": None}

    # ------------------------------------------------------------------
    # advance
    # ------------------------------------------------------------------

    async def advance_node(state: WorkflowGraphState) -> dict[str, Any]:
        """Increment the step counter and clear HITL state.

        LLM fallback path: when llm_retry_pending was True, don't increment the
        step — instead inject llm_provider_override so the same KIO reruns with
        the fallback model.
        """
        session_id = state["session_id"]

        if state.get("llm_retry_pending"):
            cfg = get_settings()
            feedback = (state.get("feedback") or "").lower().strip()
            rejected = feedback in ("no", "reject", "cancel", "deny", "decline")
            if rejected:
                if session_id in active:
                    active[session_id]["status"] = "FAILED"
                return {
                    "status": "FAILED",
                    "llm_retry_pending": False,
                    "pending_checkpoint_id": None,
                }

            fallback = cfg.llm_provider_fallback or "anthropic"
            logger.info("[{}] LLM fallback approved → retrying with {}", session_id[:8], fallback)
            _emit(
                "LLM_FALLBACK",
                session_id,
                f"Switching to {fallback!r} for retry…",
                {"fallback_provider": fallback},
            )
            if session_id in active:
                active[session_id]["llm_provider_override"] = fallback
                active[session_id]["status"] = "ACTIVE"
                active[session_id]["pending_checkpoint_id"] = None
            return {
                "llm_provider_override": fallback,
                "llm_retry_pending": False,
                "feedback": "",
                "pending_checkpoint_id": None,
                # current_step intentionally NOT incremented — same KIO runs again
            }

        next_step = state["current_step"] + 1
        if session_id in active:
            active[session_id]["progress"] = next_step
            active[session_id]["pending_checkpoint_id"] = None
            active[session_id]["status"] = "ACTIVE"
        return {"current_step": next_step, "pending_checkpoint_id": None}

    # ------------------------------------------------------------------
    # complete
    # ------------------------------------------------------------------

    async def complete_node(state: WorkflowGraphState) -> dict[str, Any]:
        """Mark the session COMPLETED and emit the final event.

        Draft reflection: if a step flagged an unresolved outcome (e.g. kio7
        reported failing tests), the run is reported as completed *with issues*
        so the result never claims success over a broken build.  The session
        status stays terminal COMPLETED so downstream consumers are unaffected.
        """
        session_id = state["session_id"]
        kio_count = len(state["kio_sequence"])
        outcome = state.get("outcome_status", "") or "OK"
        has_issues = outcome not in ("", "OK")

        await sm.update_status(session_id, "COMPLETED")
        if session_id in active:
            active[session_id]["status"] = "COMPLETED"
            active[session_id]["progress"] = kio_count
            active[session_id]["active_kio"] = None
            active[session_id]["outcome"] = outcome
        if has_issues:
            message = (
                f"Workflow COMPLETED WITH ISSUES — {outcome.replace('_', ' ').lower()} "
                f"({kio_count}/{kio_count} KIOs)."
            )
        else:
            message = f"Workflow COMPLETED — {kio_count}/{kio_count} KIOs done."
        _emit(
            "WORKFLOW_COMPLETED",
            session_id,
            message,
            {"session_id": session_id, "outcome": outcome, "has_issues": has_issues},
        )
        # Notify orchestrator state machine
        try:
            from .orchestrator_state import get_orchestrator_sm

            get_orchestrator_sm().workflow_finished()
        except Exception:
            pass
        # Clean up retry state for this session
        from .retry_manager import get_retry_manager

        get_retry_manager().cleanup(session_id)
        # Release in-process state after completion; durable state lives in PostgreSQL.
        active.pop(session_id, None)
        return {"status": "COMPLETED"}

    # ------------------------------------------------------------------
    # plan_review (confidence-gated HITL)
    # ------------------------------------------------------------------

    async def plan_review_node(state: WorkflowGraphState) -> dict[str, Any]:
        """Pause for human approval when routing confidence is low.

        Entered only when the planner fell back to a default route
        (``plan_confidence_low``).  Blocks via interrupt() *before* the first KIO
        runs, so a misrouted low-confidence pipeline can't act until a human
        approves it.  Resumes (→ run_kio) when the orchestrator calls approve();
        the operator cancels via the existing /cancel endpoint to abort.
        """
        from langgraph.types import interrupt

        session_id = state["session_id"]
        seq = state.get("kio_sequence", [])
        arrow = " → ".join(k.upper() for k in seq)
        hitl_q = (
            "Routing confidence is LOW — the planner fell back to a default route. "
            f"Proposed pipeline: {arrow}. Approve to run it, or cancel the workflow."
        )

        checkpoint = await sm.create_hitl_checkpoint(
            session_id, step="plan_review", artifact_id=str(uuid.uuid4())
        )
        checkpoint_id: str = checkpoint["checkpoint_id"]

        if session_id in active:
            active[session_id]["status"] = "BLOCKED"
            active[session_id]["pending_checkpoint_id"] = checkpoint_id

        _emit(
            "WORKFLOW_BLOCKED",
            session_id,
            "Workflow blocked — low routing confidence, awaiting plan approval",
            {"checkpoint_id": checkpoint_id, "reason": "low_routing_confidence"},
        )
        _emit(
            "HITL_CHECKPOINT",
            session_id,
            f"[HITL] {hitl_q}",
            {
                "checkpoint_id": checkpoint_id,
                "hitl_question": hitl_q,
                "kio_sequence": seq,
                "reason": "low_routing_confidence",
            },
        )

        feedback = interrupt(
            {
                "checkpoint_id": checkpoint_id,
                "hitl_question": hitl_q,
                "reason": "low_routing_confidence",
            }
        )
        if session_id in active:
            active[session_id]["status"] = "ACTIVE"
            active[session_id]["pending_checkpoint_id"] = None
        return {"feedback": feedback or "", "pending_checkpoint_id": None}

    # ------------------------------------------------------------------
    # Conditional edge functions
    # ------------------------------------------------------------------

    def should_review_plan(state: WorkflowGraphState) -> str:
        """Gate a HITL plan review when routing confidence is low."""
        return "plan_review" if state.get("plan_confidence_low") else "run_kio"

    def should_hitl(state: WorkflowGraphState) -> str:
        """Route to 'hitl' if the KIO requested review or an LLM retry is pending."""
        if state.get("llm_retry_pending"):
            return "hitl"
        step = state["current_step"]
        kio_id = state["kio_sequence"][step]
        job_status = state.get("last_result", {}).get("payload", {}).get("status", "DONE")
        if job_status == "REVIEW_REQUIRED" or kio_id in state.get("hitl_after", []):
            return "hitl"
        return "advance"

    def should_continue(state: WorkflowGraphState) -> str:
        """Route to 'run_kio' if more steps remain, otherwise 'complete'."""
        if state["current_step"] < len(state["kio_sequence"]):
            return "run_kio"
        return "complete"

    return {
        "plan": plan_node,
        "run_kio": run_kio_node,
        "plan_review": plan_review_node,
        "hitl": hitl_node,
        "advance": advance_node,
        "complete": complete_node,
        "should_review_plan": should_review_plan,
        "should_hitl": should_hitl,
        "should_continue": should_continue,
    }
