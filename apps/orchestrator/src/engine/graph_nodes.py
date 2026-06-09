"""LangGraph node factories for the KIO workflow graph.

Each node is a coroutine that receives the full WorkflowGraphState and returns
a partial dict with only the keys it wants to update.  Service clients and the
event bus are injected via closures built by ``make_nodes()``.
"""
from __future__ import annotations

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
        """Plan the KIO sequence via LM Engine if none was supplied."""
        if state.get("kio_sequence"):
            return {}
        session_id = state["session_id"]
        _emit("PLANNING_STARTED", session_id, "Planning workflow via LM Engine…")
        kio_seq, reasoning = await lm.plan_workflow(state["description"], session_id)
        arrow = " → ".join(k.upper() for k in kio_seq)
        _emit("PLANNING_DONE", session_id, f"Planned pipeline: {arrow}",
              {"kio_sequence": kio_seq, "reasoning": reasoning})
        if session_id in active:
            active[session_id]["kio_sequence"] = kio_seq
            active[session_id]["total"] = len(kio_seq)
        return {"kio_sequence": kio_seq}

    # ------------------------------------------------------------------
    # run_kio
    # ------------------------------------------------------------------

    async def run_kio_node(state: WorkflowGraphState) -> dict[str, Any]:
        """Dispatch the current KIO shell, register its artifact, and return the result."""
        session_id = state["session_id"]
        workflow_id = state["workflow_id"]
        step = state["current_step"]
        kio_seq = state["kio_sequence"]
        kio_id = kio_seq[step]

        _emit("KIO_STARTED", session_id,
              f"Initiating {kio_id.upper()}… ({step + 1}/{len(kio_seq)})",
              {"kio": kio_id, "step": step + 1, "total": len(kio_seq)})
        if session_id in active:
            active[session_id]["active_kio"] = kio_id
            active[session_id]["progress"] = step

        last_artifact = (
            state.get("last_result", {}).get("payload", {}).get("artifact_data", {})
        )
        try:
            result = await kio.execute(
                kio_id=kio_id,
                session_id=session_id,
                workflow_id=workflow_id,
                payload={
                    "description": state["description"],
                    "working_directory": state["working_directory"],
                    "feedback": state.get("feedback", ""),
                    "last_artifact": last_artifact,
                    "initial_context": state.get("initial_context", {}),
                    "llm_provider_override": state.get("llm_provider_override", ""),
                },
            )
        except Exception as exc:
            cfg = get_settings()
            already_retried = bool(state.get("llm_provider_override"))
            if cfg.llm_provider_fallback and not already_retried:
                logger.warning(
                    "[{}] {} failed with primary LLM ({}); offering HITL fallback to {}",
                    session_id[:8], kio_id, exc, cfg.llm_provider_fallback,
                )
                _emit("KIO_FAILED", session_id,
                      f"[{kio_id.upper()}] failed — offering switch to {cfg.llm_provider_fallback}",
                      {"kio": kio_id, "error": str(exc)})
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
                },
            )
        except Exception as reg_exc:
            # Non-fatal: the workflow continues, but the artifact may be missing
            # from Session Manager. Log as error so operators can investigate.
            logger.error(
                "[{}] Failed to register artifact for {} ({}); workflow continues",
                session_id[:8], kio_id, reg_exc,
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
            await sm.update_progress(session_id, {
                "completed_kios": step + 1,
                "total_kios": len(new_seq),
                "last_kio": kio_id,
            })
            _emit("KIO_DONE", session_id, f"[{kio_id.upper()}] {kio_message}",
                  {"kio": kio_id, "artifact_id": artifact_id, "step": step + 1,
                   "kio_sequence": new_seq})
            update: dict = {
                "last_result": result,
                "artifacts": [artifact_id],
                "feedback": "",
                "kio_sequence": new_seq,
            }
            if new_hitl is not None:
                update["hitl_after"] = new_hitl
            return update

        await sm.update_progress(session_id, {
            "completed_kios": step + 1,
            "total_kios": len(kio_seq),
            "last_kio": kio_id,
        })
        _emit("KIO_DONE", session_id, f"[{kio_id.upper()}] {kio_message}",
              {"kio": kio_id, "artifact_id": artifact_id, "step": step + 1})

        return {"last_result": result, "artifacts": [artifact_id], "feedback": ""}

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
        hitl_q = state.get("last_result", {}).get("payload", {}).get(
            "hitl_question", f"Review {kio_id.upper()} output before continuing?"
        )

        checkpoint = await sm.create_hitl_checkpoint(
            session_id, step=f"post_{kio_id}", artifact_id=artifact_id
        )
        checkpoint_id: str = checkpoint["checkpoint_id"]

        if session_id in active:
            active[session_id]["status"] = "PENDING_REVIEW"
            active[session_id]["pending_checkpoint_id"] = checkpoint_id

        _emit("HITL_CHECKPOINT", session_id, f"[HITL] {hitl_q}",
              {"kio": kio_id, "checkpoint_id": checkpoint_id,
               "artifact_id": artifact_id, "hitl_question": hitl_q})

        # Pause — LangGraph saves state to MemorySaver; runner resumes via Command(resume=…)
        feedback = interrupt({
            "checkpoint_id": checkpoint_id,
            "hitl_question": hitl_q,
            "kio": kio_id,
        })
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
                return {"status": "FAILED", "llm_retry_pending": False, "pending_checkpoint_id": None}

            fallback = cfg.llm_provider_fallback or "anthropic"
            logger.info("[{}] LLM fallback approved → retrying with {}", session_id[:8], fallback)
            _emit("LLM_FALLBACK", session_id,
                  f"Switching to {fallback!r} for retry…",
                  {"fallback_provider": fallback})
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
        """Mark the session COMPLETED in Session Manager and emit the final event."""
        session_id = state["session_id"]
        kio_count = len(state["kio_sequence"])
        await sm.update_status(session_id, "COMPLETED")
        if session_id in active:
            active[session_id]["status"] = "COMPLETED"
            active[session_id]["progress"] = kio_count
            active[session_id]["active_kio"] = None
        _emit("WORKFLOW_COMPLETED", session_id,
              f"Workflow COMPLETED — {kio_count}/{kio_count} KIOs done.",
              {"session_id": session_id})
        # Release in-process state after completion; durable state lives in PostgreSQL.
        active.pop(session_id, None)
        return {"status": "COMPLETED"}

    # ------------------------------------------------------------------
    # Conditional edge functions
    # ------------------------------------------------------------------

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
        "hitl": hitl_node,
        "advance": advance_node,
        "complete": complete_node,
        "should_hitl": should_hitl,
        "should_continue": should_continue,
    }
