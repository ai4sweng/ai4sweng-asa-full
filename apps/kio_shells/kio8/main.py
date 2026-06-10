"""KIO8 — Evidence Report Agent

Aggregates kio7 test results (via last_artifact) and generates a structured,
human-readable evidence report suitable for audit and sign-off.
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

KIO_ID = "kio8"
TITLE = "Evidence Report Agent"

SYSTEM_PROMPT = """\
You are a senior technical writer producing an engineering evidence report.
Given a pipeline summary (bugs found, patches applied, test results), write
a structured report with the following sections:

1. Executive Summary (2-3 sentences)
2. Findings (bullet list of confirmed bugs with severity)
3. Remediation (what was patched and how)
4. Test Evidence (pass/fail counts, coverage)
5. Verdict: PASS or FAIL with justification

Return ONLY valid JSON, no markdown fences:
{
  "executive_summary": "...",
  "findings": ["..."],
  "remediation": "...",
  "test_evidence": "...",
  "verdict": "PASS" or "FAIL",
  "verdict_reason": "..."
}"""


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
    description = payload.get("description", "")
    last_artifact = payload.get("last_artifact", {})

    passed = last_artifact.get("passed", 0)
    failed = last_artifact.get("failed", 0)
    total = last_artifact.get("total", passed + failed)
    coverage = last_artifact.get("coverage_pct", 0)
    interpretation = last_artifact.get("interpretation", "")

    logger.info("[kio8] Building evidence report. Tests: {}/{} passed", passed, total)

    _js = None
    try:
        from shared.messaging.jetstream import get_jetstream

        _js = await get_jetstream()
    except Exception:
        pass

    await publish_progress(KIO_ID, envelope.session_id, 10, "Compiling pipeline evidence…", _js)

    report: dict = {}

    try:
        llm_override = payload.get("llm_provider_override", "")
        provider = await _get_provider(llm_override)

        context = {
            "task": description,
            "test_results": {
                "passed": passed,
                "failed": failed,
                "total": total,
                "coverage_pct": coverage,
            },
            "interpretation": interpretation,
            "pipeline": ["kio2", "kio3", "kio4", "kio5", "kio6", "kio7", "kio8"],
        }

        user_prompt = (
            f"Pipeline execution context:\n{json.dumps(context, indent=2)}\n\n"
            "Generate the evidence report."
        )

        await publish_progress(
            KIO_ID, envelope.session_id, 45, "Generating evidence report with LLM…", _js
        )
        response = await provider.complete(user_prompt, system=SYSTEM_PROMPT)
        await publish_progress(KIO_ID, envelope.session_id, 80, "Parsing report…", _js)
        report = json.loads(_strip_fences(response.content))

    except Exception as exc:
        logger.exception("[kio8] LLM report generation failed: {}", exc)
        verdict = "PASS" if failed == 0 else "FAIL"
        report = {
            "executive_summary": f"Pipeline completed for: {description[:100]}",
            "findings": [],
            "remediation": "See kio6 patches.",
            "test_evidence": f"{passed}/{total} tests passed, coverage {coverage}%.",
            "verdict": verdict,
            "verdict_reason": f"{'All tests passed.' if failed == 0 else f'{failed} test(s) failed.'}",
        }

    verdict = report.get("verdict", "UNKNOWN")
    artifact_data = {
        "kio": KIO_ID,
        "report": report,
        "produced_at": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "status": "DONE",
        "artifact_id": str(uuid.uuid4()),
        "artifact_data": artifact_data,
        "message": f"Evidence report complete — verdict: {verdict}.",
    }


app = make_kio_app(KIO_ID, TITLE, handler)

if __name__ == "__main__":
    cfg = get_settings()
    port = cfg.kio_port_map.get(KIO_ID, 8018)
    uvicorn.run("main:app", host=cfg.api_host, port=port, reload=False)
