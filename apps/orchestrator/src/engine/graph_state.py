"""LangGraph workflow state — single source of truth for all graph nodes."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class WorkflowGraphState(TypedDict):
    # --- Identity ---
    session_id: str
    workflow_id: str
    owner: str
    description: str
    working_directory: str

    # --- KIO pipeline ---
    kio_sequence: list[str]
    hitl_after: list[str]
    current_step: int

    # --- Execution ---
    last_result: dict[str, Any]
    # operator.add reducer: nodes append rather than replace
    artifacts: Annotated[list[str], operator.add]
    feedback: str
    status: str
    error: str | None
    # Set by draft reflection when a step signals an unresolved result
    # (e.g. kio7 reports failing tests); read by complete_node to flag the run.
    outcome_status: str

    # --- HITL ---
    pending_checkpoint_id: str | None
    # True when the planner fell back to a default route (low routing confidence);
    # gates a plan-review HITL checkpoint before the first KIO runs.
    plan_confidence_low: bool

    # --- Prompt router ---
    # Injected by /workflow/prompt; passed to kio1 so it can route on code+prompt
    initial_context: dict[str, Any]

    # --- LLM fallback (HITL-driven retry with different provider) ---
    llm_provider_override: str  # "" = use env default, "anthropic" = use Claude fallback
    llm_retry_pending: bool  # True when last KIO failed and is awaiting HITL retry approval

    # --- Tracing ---
    correlation_id: str  # uuid generated once per workflow run; ties all envelopes together

    # --- Task config ---
    timeout_seconds: int | None  # per-task deadline; None = use global kio_client_timeout
    priority: int  # dispatch priority 1 (lowest) … 10 (highest); default 5
