"""HTTP client for the LM Engine service."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
from loguru import logger

from shared.config import get_settings
from shared.llm.llm_json_coerce import extract_json_object

from .fewshot_store import format_examples, retrieve

_VALID_KIOS = {f"kio{n}" for n in range(2, 14)}
_FALLBACK_SEQUENCE = ["kio3", "kio5"]

# Upper bound on a planned pipeline. There are ~12 routable KIOs, and a large
# multi-goal request (analyze + secure + test + patch + build + optimize + report)
# can legitimately need most of them — the old cap of 8 silently truncated the
# tail (often dropping kio8, the report). Kept as a guard against a runaway/looped
# sequence rather than as a tight limit.
_MAX_KIO_SEQUENCE_LEN = 12

# The evidence report (kio8) is the terminal deliverable — never let length
# capping drop it when the planner asked for it.
_REPORT_KIO = "kio8"

# Most options we surface when asking the user to clarify a vague request.
_MAX_CLARIFY_OPTIONS = 4


@dataclass(frozen=True)
class Clarification:
    """A request to ask the user what they meant before planning.

    ``options`` are up to four concrete suggested answers; the UI always offers
    an additional free-text "Other" choice, so callers must not add one.
    """

    question: str
    options: list[str]


@dataclass(frozen=True)
class PlanCritique:
    """A second-opinion review of a proposed KIO pipeline.

    ``verdict`` is one of:
      • ``ok``        — the plan is the minimal correct set; keep it.
      • ``adjust``    — a better pipeline is in ``revised_sequence``; apply it.
      • ``uncertain`` — genuinely ambiguous which agents fit; a human should review.
    """

    verdict: str
    revised_sequence: list[str] | None
    reason: str


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


def _normalize_kio_list(raw: object, valid_kios: set[str]) -> list[str]:
    """Filter to valid KIO IDs, de-duplicate preserving order, and cap length.

    If the capped result would drop the report agent (kio8) that the planner
    actually selected, the report is re-instated in the final slot — it is the
    terminal deliverable and must not be lost to truncation.
    """
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for k in raw:
        k = str(k).strip().lower()
        if k in valid_kios and k not in seen:
            seen.add(k)
            out.append(k)
    if len(out) > _MAX_KIO_SEQUENCE_LEN:
        capped = out[:_MAX_KIO_SEQUENCE_LEN]
        if _REPORT_KIO in out and _REPORT_KIO not in capped:
            capped[-1] = _REPORT_KIO  # preserve the report as the final step
        out = capped
    return out


class LmEngineClient:
    def __init__(self) -> None:
        cfg = get_settings()
        self._client = httpx.AsyncClient(
            base_url=cfg.lm_engine_url, timeout=float(cfg.lm_engine_client_timeout)
        )
        # Lazily-built dedicated planner model (Claude Haiku when configured).
        self._planner = None
        self._planner_lock = asyncio.Lock()

    async def _planner_provider(self):
        """Return the dedicated planner LLM provider, or None to use LM Engine.

        Enabled by ``planner_provider`` ("anthropic"/"claude") + an API key. The
        plan stage is low-volume structured JSON, where a strong hosted model is
        cheap and far more reliable than the local model — but it is never
        required: ``_complete`` falls back to the LM Engine on any error.
        """
        cfg = get_settings()
        if (cfg.planner_provider or "").strip().lower() not in ("anthropic", "claude"):
            return None
        if not cfg.anthropic_api_key:
            return None
        if self._planner is None:
            async with self._planner_lock:
                if self._planner is None:
                    from shared.llm.claude_provider import ClaudeProvider

                    model = cfg.planner_model or cfg.anthropic_model or "claude-haiku-4-5"
                    self._planner = ClaudeProvider(model=model, api_key=cfg.anthropic_api_key)
                    logger.info("Planner model: Claude {} (fallback: LM Engine)", model)
        return self._planner

    async def _complete(self, prompt: str, system: str, caller: str) -> str:
        """Run one planner-stage completion.

        Prefers the dedicated planner model (Haiku) when configured; falls back
        to the LM Engine HTTP service (local model) on any error or empty reply,
        so planning stays offline-capable.
        """
        provider = await self._planner_provider()
        if provider is not None:
            try:
                resp = await provider.complete(prompt, system=system)
                content = (resp.content or "").strip()
                if content:
                    return content
                logger.warning("[{}] planner model returned empty content; using LM Engine", caller)
            except Exception as exc:
                logger.warning("[{}] planner model failed ({}); using LM Engine", caller, exc)
        r = await self._client.post(
            "/llm/complete",
            json={"prompt": prompt, "system": system, "caller": caller},
        )
        r.raise_for_status()
        return r.json().get("content", "")

    async def assess_clarification(
        self, description: str, session_id: str
    ) -> Clarification | None:
        """Decide whether the task is clear enough to plan a pipeline.

        Returns a :class:`Clarification` (a question + up to four suggested
        answers) when the description is too vague, off-topic, or non-actionable
        to route — so the orchestrator can ASK the user instead of hallucinating
        a pipeline (e.g. "I want you to cook fish").  Returns ``None`` when the
        task is clear enough to plan.

        Fails open: any error in detection returns ``None`` so a flaky intake
        check can never block an otherwise-valid request from being planned.
        """
        text = (description or "").strip()
        # Deterministic guard: nothing actionable to plan at all.
        if len(text) < 3:
            return Clarification(
                question=(
                    "What would you like me to do? The request was empty or too "
                    "short to act on."
                ),
                options=[
                    "Analyze an existing repository for bugs",
                    "Generate new code from a specification",
                    "Run a security audit (OWASP)",
                    "Generate a test suite",
                ],
            )

        system = (
            "You are the intake gate for an AI software-engineering platform. "
            "The platform can: analyze a repository, generate new code, find and "
            "patch bugs, generate and run tests, run security/OWASP audits, and "
            "optimise ML models for energy efficiency.\n\n"
            "Decide whether the user's message is a CLEAR, actionable software-"
            "engineering task. It is NOT clear if it is gibberish, off-topic (e.g. "
            "cooking, jokes), empty, or too vague to choose any action (e.g. "
            "'help me', 'do something').\n\n"
            "If it IS a clear software task, respond with ONLY:\n"
            '{"needs_clarification": false}\n\n'
            "If it is NOT clear, ask the user what they mean. Respond with ONLY:\n"
            '{"needs_clarification": true, "question": "<one short question>", '
            '"options": ["<choice 1>", "<choice 2>", "<choice 3>", "<choice 4>"]}\n'
            "Give up to 4 concrete, distinct choices the user might have meant, "
            "phrased as actions this platform can take. The UI always adds an "
            "'Other' free-text choice, so do not include one."
        )
        try:
            raw: str = await self._complete(description, system, "orchestrator-intake")
            data = extract_json_object(raw)
            if not data or not data.get("needs_clarification"):
                return None
            question = str(data.get("question", "")).strip() or (
                "Could you clarify what you'd like me to do?"
            )
            options = [
                str(o).strip() for o in (data.get("options") or []) if str(o).strip()
            ][:_MAX_CLARIFY_OPTIONS]
            logger.info(
                "[{}] intake gate requests clarification: {}", session_id[:8], question
            )
            return Clarification(question=question, options=options)
        except Exception as exc:
            logger.warning(
                "clarification assessment failed ({}); proceeding to plan", exc
            )
            return None

    async def critique_plan(
        self,
        description: str,
        proposed: list[str],
        signals: str | None,
        session_id: str,
        rejected: list[dict] | None = None,
    ) -> PlanCritique | None:
        """Second-opinion review of a proposed pipeline.

        Returns a :class:`PlanCritique` (keep / adjust / escalate-to-human), or
        ``None`` on any failure — fail-open, so a flaky critic never blocks a
        plan that planning already produced.  Any ``revised_sequence`` is filtered
        to valid KIO IDs, de-duplicated, and length-capped before it is returned.
        """
        live_catalog, live_ids = _live_capability_catalog()
        valid_kios = _VALID_KIOS | live_ids
        signals_block = f"\n\n{signals}" if signals else ""

        system = (
            "You are a plan critic for an AI software-engineering platform. "
            "Another planner proposed a sequence of KIO agents for the user's "
            "task. Judge whether it is the MINIMAL CORRECT set: nothing essential "
            "missing, nothing redundant, in a sensible order.\n\n"
            "Agents:\n"
            "- kio3 Repo Analyzer (EXISTING code); kio4 Test Generator; "
            "kio5 Bug Detector;\n"
            "- kio6 Patch (pair with kio7); kio7 Test Re-run; kio8 Report;\n"
            "- kio9 Code Generator (NEW code, none given); kio10 TinyML/energy;\n"
            "- kio11 AI Test Automation; kio12 Security/OWASP."
            f"{signals_block}\n\n"
            "Respond with ONLY JSON — no other text:\n"
            '{"verdict": "ok|adjust|uncertain", "revised_sequence": ["kioN", ...], '
            '"reason": "one sentence"}\n'
            "- 'ok': plan is fine; set revised_sequence to the same list.\n"
            "- 'adjust': you are CONFIDENT in a better list; put it in "
            "revised_sequence.\n"
            "- 'uncertain': genuinely ambiguous which agents fit — a human should "
            "review; revised_sequence may repeat the proposal.\n"
            "Only use valid KIO IDs: "
            + ", ".join(sorted(valid_kios, key=_kio_sort_key))
            + "."
        )
        rejected_line = ""
        if rejected:
            pairs = "; ".join(
                f"{r.get('kio')} ({r.get('reason', '')})" for r in rejected if r.get("kio")
            )
            if pairs:
                rejected_line = (
                    f"\nThe planner considered but REJECTED: {pairs}\n"
                    "If any rejection was wrong, correct it."
                )
        user = (
            f"Task: {description}\n"
            f"Proposed pipeline: {list(proposed)}"
            f"{rejected_line}\n"
            "Critique it."
        )
        try:
            raw: str = await self._complete(user, system, "orchestrator-critic")
            data = extract_json_object(raw)
            if not data:
                return None

            verdict = str(data.get("verdict", "")).strip().lower()
            if verdict not in ("ok", "adjust", "uncertain"):
                verdict = "ok"

            revised = _normalize_kio_list(data.get("revised_sequence"), valid_kios) or None

            reason = str(data.get("reason", "")).strip()
            logger.info(
                "[{}] plan critic verdict={} reason={}",
                session_id[:8],
                verdict,
                reason[:120],
            )
            return PlanCritique(verdict=verdict, revised_sequence=revised, reason=reason)
        except Exception as exc:
            logger.warning("plan critique failed ({}); keeping original plan", exc)
            return None

    async def plan_workflow(
        self,
        description: str,
        session_id: str,
        examples: str | None = None,
        signals: str | None = None,
        bids: str | None = None,
        intent: str | None = None,
    ) -> tuple[list[str], str, bool, list[dict]]:
        """Ask LM Engine which KIOs to run. Falls back to kio3→kio5 on any failure.

        ``signals`` is an optional read-only repo-recon fact sheet (languages,
        tests present, security surfaces, …); when provided it grounds agent
        selection in what the code actually is instead of the prompt text alone.
        ``bids`` is an optional ranked shortlist of online agents whose advertised
        capabilities match the task (see ``capability_bidder``).

        Returns ``(kio_sequence, reasoning, used_fallback, rejected)``.
        ``used_fallback`` is True when planning failed and the default route was
        substituted — a low-confidence signal the orchestrator uses to gate a HITL
        plan review.  ``rejected`` is the planner's list of
        ``{"kio", "reason"}`` candidates it considered but left out, so the plan
        critic (and humans) can challenge an omission.

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
        # The caller (plan_node) may pre-retrieve and pass them so it can also
        # surface them as an event; otherwise we retrieve here.
        examples_block = (
            examples if examples is not None else format_examples(retrieve(description, k=3))
        )
        signals_block = f"\n\n{signals}" if signals else ""
        bids_block = f"\n\n{bids}" if bids else ""
        intent_block = f"\n\n{intent}" if intent else ""

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
            "- kio10: TinyML / Energy Efficiency — optimises ML models for "
            "energy-constrained or edge deployment\n"
            "- kio11: AI Test Automation — comprehensive AI-powered test suites "
            "(broader/ongoing automation beyond kio4)\n"
            "- kio12: AI Cybersecurity — OWASP-based security audit and hardening\n"
            "- kio13: Developer Training — guidance on using the AI tooling "
            "(rarely part of an automated build/analysis pipeline)\n"
            f"{intent_block}"
            f"{live_block}"
            f"{signals_block}"
            f"{bids_block}\n\n"
            "ROUTING:\n"
            "- The user asks to BUILD/WRITE/CREATE a program, application, app, tool, "
            "script, function, class, or module (and gives NO code) → this is CODE "
            "GENERATION → [\"kio9\"]. Do NOT use kio3/kio4/kio5 — there is nothing to "
            "analyze yet.\n"
            "- Build it AND check for bugs → [\"kio9\", \"kio5\"]\n"
            "- Find bugs / analyze an EXISTING repo or code → [\"kio3\", \"kio5\"]\n"
            "- Fix/patch existing code → [\"kio5\", \"kio6\", \"kio7\"]\n"
            "- Full pipeline on a repo → [\"kio3\", \"kio4\", \"kio5\", \"kio6\", \"kio7\", \"kio8\"]\n"
            "- When unsure and no code is given → [\"kio9\"]\n"
            "USING REPOSITORY SIGNALS (when provided above):\n"
            "- security-sensitive surfaces true → include kio12 (security/OWASP audit)\n"
            "- has tests false and the task cares about correctness → include kio4 "
            "(generate tests)\n"
            "- files scanned 0 / no repo signals → treat as code generation, not "
            "analysis\n"
            "- prefer agents listed under 'Capability bids' when they fit the task; "
            "they are online and self-advertised as relevant\n"
            "- task intent type 'security' or risk 'high' → include kio12; "
            "'optimization' → include kio10; 'code_generation' with no existing "
            "code → kio9\n\n"
            f"{examples_block}\n\n"
            "Respond with ONLY a JSON object — no other text:\n"
            '{"kio_sequence": ["kio9"], "reasoning": "one sentence", '
            '"rejected": [{"kio": "kio3", "reason": "no existing code to analyze"}]}\n'
            "In 'rejected', list any plausible agents you considered but left out, "
            "each with a one-line reason (empty list if none).\n"
            "Valid KIO IDs: "
            + ", ".join(sorted(valid_kios, key=_kio_sort_key))
            + ". Pick only those needed. Return at most 12 KIOs. If the task "
            "calls for a final report, end the pipeline with kio8."
        )
        for attempt in range(2):
            try:
                raw: str = await self._complete(description, system, "orchestrator-planner")
                plan = extract_json_object(raw)  # robust multi-strategy parser
                if plan is None:
                    raise ValueError(f"LM returned non-JSON content: {raw[:200]!r}")

                # Normalise: accept "kio_sequence" or common alias "kios"
                seq_raw = plan.get("kio_sequence") or plan.get("kios") or []
                if not isinstance(seq_raw, list):
                    raise ValueError(f"kio_sequence is not a list: {seq_raw!r}")

                # Filter invalid IDs, deduplicate, cap length, preserve the report
                kios = _normalize_kio_list(seq_raw, valid_kios)

                reasoning: str = str(plan.get("reasoning", "")).strip()

                if not kios:
                    raise ValueError("LM returned no valid KIO IDs")

                # Considered-but-rejected candidates (feeds the plan critic).
                rejected: list[dict] = []
                rejected_raw = plan.get("rejected")
                if isinstance(rejected_raw, list):
                    for item in rejected_raw:
                        if not isinstance(item, dict):
                            continue
                        kid = str(item.get("kio", "")).strip().lower()
                        if kid in valid_kios and kid not in kios:
                            rejected.append(
                                {"kio": kid, "reason": str(item.get("reason", "")).strip()}
                            )
                        if len(rejected) >= _MAX_KIO_SEQUENCE_LEN:
                            break

                logger.info(
                    "LM planned KIO sequence: {} (attempt {})",
                    " → ".join(k.upper() for k in kios),
                    attempt + 1,
                )
                return kios, reasoning, False, rejected

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
        return list(_FALLBACK_SEQUENCE), "Fallback: kio3 → kio5 (LM planning failed)", True, []

    async def close(self) -> None:
        await self._client.aclose()


_client: LmEngineClient | None = None


def get_lm_client() -> LmEngineClient:
    global _client
    if _client is None:
        _client = LmEngineClient()
    return _client
