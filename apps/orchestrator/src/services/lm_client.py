"""HTTP client for the LM Engine service."""

from __future__ import annotations


import httpx
from loguru import logger

from shared.config import get_settings
from shared.llm.llm_json_coerce import extract_json_object

from .fewshot_store import format_examples, retrieve

_VALID_KIOS = {f"kio{n}" for n in range(2, 14)}
_FALLBACK_SEQUENCE = ["kio3", "kio5"]

# qwen3b sometimes returns duplicate KIOs or very long sequences.
_MAX_KIO_SEQUENCE_LEN = 8


def _kio_sort_key(kio_id: str) -> tuple[int, object]:
    """Sort kio2 < kio9 < kio10 (numeric), falling back to lexical for odd ids."""
    suffix = kio_id[3:]
    return (0, int(suffix)) if suffix.isdigit() else (1, kio_id)


def _live_capability_catalog() -> tuple[str, set[str]]:
    """Build a planner catalog from live CAPABILITY_ANNOUNCEMENTs.

    Reads the orchestrator's AgentRegistry (populated over NATS) so newly
    announced KIOs — including partner-provided ones not in the static
    descriptions below — become visible to the planner and routable.

    Returns ``(catalog_text, live_kio_ids)``.  Both are empty when no agent is
    announced (e.g. HTTP/dev mode without NATS); the caller then falls back to
    the static catalog so routing keeps working.
    """
    try:
        from ..engine.agent_registry import get_agent_registry

        agents = [a for a in get_agent_registry().list_agents() if a.get("alive")]
    except Exception as exc:  # registry unavailable → static fallback
        logger.debug("plan_workflow: live capability lookup failed ({}); using static catalog", exc)
        return "", set()

    if not agents:
        return "", set()

    live_ids: set[str] = set()
    lines = ["Currently ONLINE KIO agents (announced this run — prefer these when they fit):"]
    for agent in sorted(agents, key=lambda a: _kio_sort_key(a.get("kio_id", ""))):
        kio_id = agent.get("kio_id", "")
        if not kio_id:
            continue
        live_ids.add(kio_id)
        tasks = agent.get("supported_tasks") or []
        desc = "; ".join(
            str(t.get("description", "")).strip()
            for t in tasks
            if isinstance(t, dict) and t.get("description")
        )
        lines.append(f"- {kio_id}: {desc or kio_id}")
    return "\n".join(lines), live_ids


class LmEngineClient:
    def __init__(self) -> None:
        cfg = get_settings()
        self._client = httpx.AsyncClient(
            base_url=cfg.lm_engine_url, timeout=float(cfg.lm_engine_client_timeout)
        )

    async def plan_workflow(
        self, description: str, session_id: str
    ) -> tuple[list[str], str, bool]:
        """Ask LM Engine which KIOs to run. Falls back to kio3→kio5 on any failure.

        Returns ``(kio_sequence, reasoning, used_fallback)``.  ``used_fallback`` is
        True when planning failed and the default route was substituted — a
        low-confidence signal the orchestrator uses to gate a HITL plan review.

        Robust against common qwen3b hallucinations:
          • Markdown-fenced JSON     → extract_json_object strips fences
          • Python literals (None/True/False) → repair_json_text normalises
          • Truncated JSON           → _close_unclosed_json appends missing brackets
          • Wrong field names        → checked via .get() with defaults
          • Duplicate KIOs           → deduplicated while preserving order
          • Invalid KIO IDs          → filtered against _VALID_KIOS
          • Empty / oversized output → falls back to _FALLBACK_SEQUENCE
        """
        live_catalog, live_ids = _live_capability_catalog()
        valid_kios = _VALID_KIOS | live_ids
        live_block = f"\n\n{live_catalog}" if live_catalog else ""
        # Dynamic few-shot: pull the exemplars most similar to this description.
        examples_block = format_examples(retrieve(description, k=3))

        system = (
            "You are a workflow planner for an AI software-engineering platform. "
            "Choose which KIO agents to run for the user's task.\n\n"
            "What each KIO does:\n"
            "- kio9: Code Generator — writes NEW code from a description (use when the "
            "user wants software built and NO existing code is given)\n"
            "- kio3: Repository Analyzer — reads an EXISTING codebase from disk\n"
            "- kio4: Test Generator — writes pytest tests for code\n"
            "- kio5: Bug Detector — finds bugs in code or in kio3 findings\n"
            "- kio6: Patch Generator — fixes confirmed bugs (always pair with kio7)\n"
            "- kio7: Test Re-runner — runs tests after patching\n"
            "- kio8: Report Generator — final analysis report\n"
            f"{live_block}\n\n"
            "ROUTING:\n"
            "- The user asks to BUILD/WRITE/CREATE a program, application, app, tool, "
            "script, function, class, or module (and gives NO code) → this is CODE "
            "GENERATION → [\"kio9\"]. Do NOT use kio3/kio4/kio5 — there is nothing to "
            "analyze yet.\n"
            "- Build it AND check for bugs → [\"kio9\", \"kio5\"]\n"
            "- Find bugs / analyze an EXISTING repo or code → [\"kio3\", \"kio5\"]\n"
            "- Fix/patch existing code → [\"kio5\", \"kio6\", \"kio7\"]\n"
            "- Full pipeline on a repo → [\"kio3\", \"kio4\", \"kio5\", \"kio6\", \"kio7\", \"kio8\"]\n"
            "- When unsure and no code is given → [\"kio9\"]\n\n"
            f"{examples_block}\n\n"
            "Respond with ONLY a JSON object — no other text:\n"
            '{"kio_sequence": ["kio9"], "reasoning": "one sentence"}\n'
            "Valid KIO IDs: "
            + ", ".join(sorted(valid_kios, key=_kio_sort_key))
            + ". Pick only those needed. Return at most 8 KIOs."
        )
        for attempt in range(2):
            try:
                resp = await self._client.post(
                    "/llm/complete",
                    json={
                        "prompt": description,
                        "system": system,
                        "caller": "orchestrator-planner",
                    },
                )
                resp.raise_for_status()
                raw: str = resp.json().get("content", "")
                plan = extract_json_object(raw)  # robust multi-strategy parser
                if plan is None:
                    raise ValueError(f"LM returned non-JSON content: {raw[:200]!r}")

                # Normalise: accept "kio_sequence" or common alias "kios"
                seq_raw = plan.get("kio_sequence") or plan.get("kios") or []
                if not isinstance(seq_raw, list):
                    raise ValueError(f"kio_sequence is not a list: {seq_raw!r}")

                # Filter invalid IDs, deduplicate preserving order, cap length
                seen: set[str] = set()
                kios: list[str] = []
                for k in seq_raw:
                    k = str(k).strip().lower()
                    if k in valid_kios and k not in seen:
                        seen.add(k)
                        kios.append(k)
                        if len(kios) >= _MAX_KIO_SEQUENCE_LEN:
                            break

                reasoning: str = str(plan.get("reasoning", "")).strip()

                if not kios:
                    raise ValueError("LM returned no valid KIO IDs")

                logger.info(
                    "LM planned KIO sequence: {} (attempt {})",
                    " → ".join(k.upper() for k in kios),
                    attempt + 1,
                )
                return kios, reasoning, False

            except Exception as exc:
                logger.warning(
                    "LM planning attempt {}/{} failed ({}); {}",
                    attempt + 1,
                    2,
                    exc,
                    "retrying…" if attempt == 0 else "using fallback",
                )

        logger.warning(
            "LM planning failed after 2 attempts — falling back to {}",
            " → ".join(k.upper() for k in _FALLBACK_SEQUENCE),
        )
        return list(_FALLBACK_SEQUENCE), "Fallback: kio3 → kio5 (LM planning failed)", True

    async def close(self) -> None:
        await self._client.aclose()


_client: LmEngineClient | None = None


def get_lm_client() -> LmEngineClient:
    global _client
    if _client is None:
        _client = LmEngineClient()
    return _client
