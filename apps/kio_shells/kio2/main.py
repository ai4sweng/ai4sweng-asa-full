"""KIO2 — Planning Agent

Uses the LLM to analyse the task description and produce a structured
workflow plan — which KIOs to run, in what order, with human-readable
reasoning for each stage.

Pipeline: kio2 → (LLM-selected stages) → kio8
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))
sys.path.insert(0, str(Path(__file__).parents[3]))

import uvicorn
from loguru import logger

from kio_base import MessageEnvelope, make_kio_app, publish_progress
from shared.llm.factory import create_llm_provider
from shared.config import get_settings

KIO_ID = "kio2"
TITLE = "Planning Agent"

FULL_PIPELINE = ["kio2", "kio3", "kio4", "kio5", "kio6", "kio7", "kio8"]

SYSTEM_PROMPT = """\
You are an AI workflow planning agent for a software-engineering automation platform.

Available KIO agents and their roles:
- kio3: Repo Analyzer — scans source files for bugs, anti-patterns, security issues
- kio4: Test Generator — generates pytest suites for the repository
- kio5: Bug Detector — validates findings via LLM analysis; always requires human approval
- kio6: Patch Agent — generates code patches for confirmed bugs
- kio7: Test Re-run — runs the patched test suite to verify fixes
- kio8: Evidence Report — produces an executive summary of the full analysis
- kio9: Code Generator — generates new code or modules from a specification
- kio10: TinyML/Energy Efficiency — optimises ML models for energy-constrained deployment
- kio11: AI Test Automation — builds comprehensive AI-powered test suites
- kio12: AI Cybersecurity — performs OWASP-based security assessment

Given the task description, select the minimal effective pipeline:
- Always start with kio3 (repo analysis) when source code is involved.
- Always end with kio8 (evidence report) as the final step.
- Include kio5 only if bug detection/security is required (it always triggers human approval).
- Include kio6+kio7 only if patching and verification are needed.
- Include kio10 only if energy efficiency or TinyML is mentioned.
- Include kio11 if comprehensive test automation beyond kio4 is needed.
- Include kio12 for explicit security audits or OWASP checks.

Return ONLY valid JSON, no markdown fences:
{
  "pipeline": ["kio2", "kio3", ...],
  "hitl_after": ["kio5"],
  "stages": [
    {"kio": "kio3", "purpose": "one sentence why this stage is needed"}
  ],
  "reasoning": "overall 2-3 sentence rationale for the plan",
  "confidence": 0.9
}

Rules:
- pipeline[0] MUST be "kio2" (the planner itself).
- pipeline[-1] MUST be "kio8" (the evidence reporter).
- hitl_after must be a subset of pipeline, usually ["kio5"] if kio5 is included.
- If no source code / repo is mentioned, use a minimal pipeline: ["kio2", "kio8"]."""


_provider = None
_provider_lock = asyncio.Lock()


async def _get_provider(override: str = ""):
    if override:
        return await create_llm_provider(override=override)
    global _provider
    if _provider is None:
        async with _provider_lock:
            if _provider is None:
                _provider = await create_llm_provider()
    return _provider


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = text[: text.rfind("```")].strip()
    return text


async def handler(envelope: MessageEnvelope) -> dict:
    payload = envelope.payload
    description = payload.get("description", "Analyse and improve the repository")
    working_dir = payload.get("working_directory", "")
    override = payload.get("llm_override", "")
    js = getattr(envelope, "_js", None)

    logger.info("[kio2] Planning pipeline for: {}", description[:100])
    await publish_progress(KIO_ID, envelope.session_id, 10, "Initialising planner", js)

    try:
        provider = await _get_provider(override)

        user_prompt = (
            f"Task: {description}\n"
            f"Repository/working directory: {working_dir or '(not specified)'}\n\n"
            "Select the optimal KIO pipeline for this task."
        )

        await publish_progress(KIO_ID, envelope.session_id, 40, "LLM generating plan", js)
        response = await provider.complete(user_prompt, system=SYSTEM_PROMPT)
        raw = _strip_fences(response.content)

        try:
            plan = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[kio2] LLM returned non-JSON, using full pipeline")
            plan = {
                "pipeline": FULL_PIPELINE,
                "hitl_after": ["kio5"],
                "stages": [],
                "reasoning": (
                    f"Default pipeline selected (LLM parse error). Task: {description[:120]}"
                ),
                "confidence": 0.5,
            }

        pipeline = plan.get("pipeline", FULL_PIPELINE)
        hitl_after = plan.get("hitl_after", ["kio5"])

        # Enforce invariants: must start with kio2, end with kio8
        if not pipeline or pipeline[0] != "kio2":
            pipeline = ["kio2"] + [k for k in pipeline if k != "kio2"]
        if "kio8" not in pipeline:
            pipeline.append("kio8")

        stages_summary = " → ".join(pipeline[1:])
        await publish_progress(KIO_ID, envelope.session_id, 90, f"Plan ready: {stages_summary}", js)

        logger.info("[kio2] Plan: {} | hitl={}", pipeline, hitl_after)

        return {
            "status": "DONE",
            "artifact_id": str(uuid.uuid4()),
            "artifact_data": {
                "kio": KIO_ID,
                "pipeline": pipeline,
                "hitl_after": hitl_after,
                "stages": plan.get("stages", []),
                "reasoning": plan.get("reasoning", ""),
                "confidence": plan.get("confidence", 1.0),
                "produced_at": datetime.now(timezone.utc).isoformat(),
            },
            "kio_sequence": pipeline,
            "hitl_after": hitl_after,
            "message": f"Pipeline planned: {stages_summary}",
        }

    except Exception as exc:
        logger.exception("[kio2] planning failed, falling back to default pipeline: {}", exc)
        stages = " → ".join(FULL_PIPELINE[1:])
        return {
            "status": "DONE",
            "artifact_id": str(uuid.uuid4()),
            "artifact_data": {
                "kio": KIO_ID,
                "pipeline": FULL_PIPELINE,
                "hitl_after": ["kio5"],
                "stages": [],
                "reasoning": f"Default pipeline (error: {exc}). Task: {description[:100]}",
                "confidence": 0.0,
                "produced_at": datetime.now(timezone.utc).isoformat(),
            },
            "kio_sequence": FULL_PIPELINE,
            "hitl_after": ["kio5"],
            "message": f"Pipeline planned (fallback): {stages}",
        }


app = make_kio_app(KIO_ID, TITLE, handler)

if __name__ == "__main__":
    cfg = get_settings()
    port = cfg.kio_port_map.get(KIO_ID, 8012)
    uvicorn.run("main:app", host=cfg.api_host, port=port, reload=False)
