"""KIO5 — Bug Detector Agent

Independently validates the findings passed through from kio3 (via kio4's
artifact) using LLM-powered analysis. Always returns REVIEW_REQUIRED so a
human approves the confirmed bug list before patch generation begins.
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

from kio_base import MessageEnvelope, make_kio_app, make_session_reader, publish_progress
from shared.llm.factory import create_llm_provider
from shared.config import get_settings

KIO_ID = "kio5"
TITLE = "Bug Detector Agent"

SYSTEM_PROMPT = """\
You are a senior software security engineer. Given a list of code findings,
validate each one and produce a confirmed bug list.

For each finding decide:
- Is it a real exploitable bug? (confirmed=true/false)
- Assign a severity: CRITICAL / HIGH / MEDIUM / LOW
- Assign the CWE identifier
- Write a one-sentence suggested fix

Return ONLY valid JSON, no markdown fences:
{
  "summary": "overall risk assessment",
  "confirmed_count": <int>,
  "bugs": [
    {
      "bug_id": "BUG-001",
      "file": "path/to/file.py",
      "line": <int>,
      "severity": "CRITICAL",
      "kind": "sql_injection",
      "description": "...",
      "cwe": "CWE-89",
      "suggested_fix": "...",
      "confirmed": true
    }
  ]
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
    # kio4 passes kio3 findings through in its own artifact;
    # kio1 (router) passes raw code directly when no upstream findings exist
    findings = last_artifact.get("findings", [])
    raw_code = last_artifact.get("code", "") or payload.get("initial_context", {}).get("code", "")

    # When the orchestrator sends input_artifact refs instead of inlining data,
    # pull those payloads from Session Manager — which reads PostgreSQL.
    # This keeps DB credentials entirely out of KIO containers.
    if not findings and not raw_code:
        input_refs = payload.get("input_artifacts", [])
        if input_refs:
            reader = make_session_reader(envelope)
            fetched = await reader.get_artifacts_batch([ref["artifact_id"] for ref in input_refs])
            for art in fetched:
                data = art.get("artifact_data", {})
                findings.extend(data.get("findings", []))
                if not raw_code:
                    raw_code = data.get("code", "")
            if findings:
                logger.info(
                    "[kio5] Pulled {} finding(s) from Session Manager via input_artifacts",
                    len(findings),
                )

    if findings:
        logger.info("[kio5] Validating {} finding(s) from upstream", len(findings))
    elif raw_code:
        logger.info("[kio5] Direct code mode — {} chars of code provided", len(raw_code))
    else:
        logger.warning("[kio5] No findings and no code — running with empty input")

    _js = None
    try:
        from shared.messaging.jetstream import get_jetstream

        _js = await get_jetstream()
    except Exception:
        pass

    await publish_progress(KIO_ID, envelope.session_id, 10, "Bug detection starting…", _js)

    bugs = []
    summary = ""

    try:
        llm_override = payload.get("llm_provider_override", "")
        provider = await _get_provider(llm_override)

        await publish_progress(
            KIO_ID, envelope.session_id, 35, "Sending code to LLM for analysis…", _js
        )

        if raw_code and not findings:
            # Direct mode: full bug detection on the provided code snippet
            user_prompt = (
                f"Task: {description}\n\n"
                f"Analyse this code for security vulnerabilities and logic bugs:\n"
                f"```python\n{raw_code[:6000]}\n```\n\n"
                "Find ALL real bugs, classify them, and return the JSON result."
            )
        else:
            findings_text = json.dumps(findings, indent=2) if findings else "[]"
            user_prompt = (
                f"Task context: {description}\n\n"
                f"Findings to validate:\n{findings_text}\n\n"
                "Confirm which are real bugs and classify them."
            )

        response = await provider.complete(user_prompt, system=SYSTEM_PROMPT)
        await publish_progress(KIO_ID, envelope.session_id, 65, "Parsing LLM response…", _js)
        result = json.loads(_strip_fences(response.content))
        if not isinstance(result, dict):
            raise ValueError(f"LLM returned non-object JSON: {type(result).__name__}")
        summary = result.get("summary", "")
        all_bugs = result.get("bugs", [])
        if not isinstance(all_bugs, list):
            raise ValueError(f"'bugs' field is not a list: {type(all_bugs).__name__}")
        bugs = [b for b in all_bugs if b.get("confirmed", True)]
        for i, bug in enumerate(bugs, 1):
            bug.setdefault("bug_id", f"BUG-{i:03d}")

    except Exception as exc:
        logger.exception("[kio5] LLM bug detection failed: {}", exc)
        summary = f"Bug detection encountered an error: {exc}"
        bugs = []

    await publish_progress(
        KIO_ID, envelope.session_id, 75, f"{len(bugs)} bug(s) confirmed — running OWASP scan…", _js
    )

    # ── A2A: call kio12 for OWASP scan on the same code ──────────────────────
    owasp_vulns: list = []
    owasp_summary: str = ""
    hardened_files: list = []
    owasp_error: str = ""

    if raw_code:
        try:
            from shared.a2a.client import get_a2a_client

            a2a = get_a2a_client()
            logger.info("[kio5] A2A → kio12 OWASP scan (session={})", envelope.session_id[:8])
            kio12_result = await a2a.invoke(
                "kio12",
                session_id=envelope.session_id,
                workflow_id=envelope.workflow_id,
                caller_kio=KIO_ID,
                payload={
                    "description": f"OWASP Top 10 scan for: {description}",
                    "code": raw_code,
                    "llm_provider_override": payload.get("llm_provider_override", ""),
                },
            )
            kio12_data = kio12_result.get("payload", {}).get("artifact_data", {})
            owasp_vulns = kio12_data.get("vulnerabilities", [])
            owasp_summary = kio12_data.get("summary", "")
            hardened_files = kio12_data.get("files", [])
            logger.info(
                "[kio5] A2A ← kio12: {} OWASP finding(s), {} hardened file(s)",
                len(owasp_vulns),
                len(hardened_files),
            )
        except Exception as exc:
            owasp_error = str(exc)
            logger.error(
                "[kio5] A2A kio12 call FAILED ({}); OWASP data unavailable — "
                "reviewer should be aware",
                exc,
            )
    # ─────────────────────────────────────────────────────────────────────────

    await publish_progress(KIO_ID, envelope.session_id, 90, "Composing bug report…", _js)

    bug_count = len(bugs)
    criticals = sum(1 for b in bugs if b.get("severity") == "CRITICAL")
    highs = sum(1 for b in bugs if b.get("severity") == "HIGH")

    owasp_notice = ""
    if owasp_error:
        owasp_notice = f" | ⚠ OWASP scan FAILED: {owasp_error[:80]}"
    elif owasp_vulns:
        owasp_notice = f" | kio12 OWASP: {len(owasp_vulns)} finding(s)"

    hitl_question = (
        f"Bug Detector confirmed {bug_count} bug(s)"
        + (f" — {criticals} CRITICAL" if criticals else "")
        + (f", {highs} HIGH" if highs else "")
        + owasp_notice
        + ". Approve patch generation?"
    )

    artifact_data = {
        "kio": KIO_ID,
        "bug_count": bug_count,
        "bugs": bugs,
        "summary": summary,
        # OWASP enrichment via A2A kio12
        "owasp_vulnerabilities": owasp_vulns,
        "owasp_summary": owasp_summary,
        "owasp_error": owasp_error,  # empty string = success; non-empty = kio12 failed
        "hardened_files": hardened_files,
        # Pass kio4's generated test files through so kio7 can write and run them
        "test_files": last_artifact.get("files", []),
        "produced_at": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "status": "REVIEW_REQUIRED",
        "artifact_id": str(uuid.uuid4()),
        "artifact_data": artifact_data,
        "message": f"Bug detection complete: {bug_count} confirmed bug(s).",
        "hitl_question": hitl_question,
    }


app = make_kio_app(KIO_ID, TITLE, handler)

if __name__ == "__main__":
    cfg = get_settings()
    port = cfg.kio_port_map.get(KIO_ID, 8015)
    uvicorn.run("main:app", host=cfg.api_host, port=port, reload=False)
