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

    # --- HITL ---
    pending_checkpoint_id: str | None

    # --- Prompt router ---
    # Injected by /workflow/prompt; passed to kio1 so it can route on code+prompt
    initial_context: dict[str, Any]

    # --- LLM fallback (HITL-driven retry with different provider) ---
    llm_provider_override: str   # "" = use env default, "anthropic" = use Claude fallback
    llm_retry_pending: bool      # True when last KIO failed and is awaiting HITL retry approval

    # --- Tracing ---
    correlation_id: str          # uuid generated once per workflow run; ties all envelopes together

    # --- Task config ---
    timeout_seconds: int | None  # per-task deadline; None = use global kio_client_timeout
