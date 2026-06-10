"""KIO13 — Developer Training for Using AI-Powered Tools

Reads pipeline artifacts from the current workflow and generates personalised
training materials: concept explanations, hands-on exercises, skill-gap
assessments, and best-practice guides tailored to the work done in this session.
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

KIO_ID = "kio13"
TITLE = "Developer Training for Using AI-Powered Tools"

SYSTEM_PROMPT = """\
You are an expert developer educator specialising in AI-powered software engineering tools.

Given a summary of what an AI pipeline found and fixed in a codebase, produce
personalised training materials that help the developer:
1. Understand WHY the issues existed and how to avoid them in future.
2. Learn to USE AI tools effectively (prompt engineering, interpreting results).
3. Apply BEST PRACTICES relevant to the issues discovered.
4. ASSESS their own skill gaps based on the issues found.

Structure your output as:
- A skill gap summary (what knowledge gaps led to these issues)
- 2-4 concept modules (each with a title, explanation, and example)
- 2-3 hands-on exercises with expected outcomes
- A reading list of 3-5 resources (titles + one-line descriptions)
- 5 quiz questions to verify understanding

Return ONLY valid JSON, no markdown fences:
{
  "skill_gaps": ["gap 1", "gap 2"],
  "modules": [
    {
      "title": "SQL Injection Prevention",
      "concept": "explanation of the concept",
      "example": "concrete code example showing the right approach",
      "ai_tool_tip": "how to use AI tools to detect/prevent this"
    }
  ],
  "exercises": [
    {
      "title": "Fix the injection vulnerability",
      "description": "what the developer should do",
      "expected_outcome": "what success looks like",
      "difficulty": "BEGINNER"
    }
  ],
  "reading_list": [
    {"title": "OWASP Top 10", "url": "https://owasp.org/Top10/", "why": "foundational security reference"}
  ],
  "quiz": [
    {
      "question": "What is parameterised query?",
      "options": ["A", "B", "C", "D"],
      "correct": 0,
      "explanation": "why A is correct"
    }
  ],
  "summary": "overall training plan summary"
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


def _build_pipeline_context(payload: dict) -> str:
    """Extract a readable summary from upstream artifacts passed in the payload."""
    parts = []

    # kio3 findings
    kio3 = payload.get("kio3_artifact", {})
    findings = kio3.get("findings", [])
    if findings:
        parts.append(f"Repository analysis found {len(findings)} issue(s):")
        for f in findings[:8]:
            parts.append(
                f"  - [{f.get('severity', '?')}] {f.get('category', '?')} "
                f"in {f.get('file', '?')}:{f.get('line', '?')} — {f.get('description', '')}"
            )

    # kio5 bugs
    kio5 = payload.get("kio5_artifact", {})
    bugs = kio5.get("bugs", [])
    if bugs:
        parts.append(f"\nBug detector confirmed {len(bugs)} bug(s):")
        for b in bugs[:6]:
            parts.append(
                f"  - [{b.get('severity', '?')}] {b.get('kind', '?')} "
                f"({b.get('cwe', '')}) — {b.get('suggested_fix', '')}"
            )

    # kio6 patches
    kio6 = payload.get("kio6_artifact", {})
    patches = kio6.get("patches", [])
    if patches:
        parts.append(f"\nPatch agent applied {len(patches)} patch(es).")

    # kio8 summary
    kio8 = payload.get("kio8_artifact", {})
    report_summary = kio8.get("executive_summary", "") or kio8.get("summary", "")
    if report_summary:
        parts.append(f"\nEvidence report summary: {report_summary[:300]}")

    if not parts:
        parts.append(
            payload.get("description", "General AI-powered software engineering pipeline.")
        )

    return "\n".join(parts)


async def handler(envelope: MessageEnvelope) -> dict:
    payload = envelope.payload
    description = payload.get("description", "")
    override = payload.get("llm_override", "")
    js = getattr(envelope, "_js", None)

    logger.info("[kio13] Generating training materials")
    await publish_progress(KIO_ID, envelope.session_id, 10, "Building pipeline context", js)

    try:
        provider = await _get_provider(override)
        pipeline_context = _build_pipeline_context(payload)

        await publish_progress(KIO_ID, envelope.session_id, 35, "Identifying skill gaps", js)

        user_prompt = (
            f"Session description: {description or 'AI-powered code analysis pipeline'}\n\n"
            f"What the pipeline found and did:\n{pipeline_context}\n\n"
            "Generate personalised developer training materials based on the above."
        )

        await publish_progress(KIO_ID, envelope.session_id, 60, "LLM generating modules", js)
        response = await provider.complete(user_prompt, system=SYSTEM_PROMPT)
        raw = _strip_fences(response.content)

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = {
                "skill_gaps": [],
                "modules": [],
                "exercises": [],
                "reading_list": [],
                "quiz": [],
                "summary": response.content[:500],
            }

        mod_count = len(result.get("modules", []))
        quiz_count = len(result.get("quiz", []))
        await publish_progress(
            KIO_ID,
            envelope.session_id,
            90,
            f"{mod_count} module(s), {quiz_count} quiz question(s)",
            js,
        )
        logger.info("[kio13] {} module(s), {} quiz question(s) generated", mod_count, quiz_count)

        return {
            "status": "DONE",
            "artifact_id": str(uuid.uuid4()),
            "artifact_data": {
                "kio": KIO_ID,
                "summary": result.get("summary", ""),
                "skill_gaps": result.get("skill_gaps", []),
                "modules": result.get("modules", []),
                "exercises": result.get("exercises", []),
                "reading_list": result.get("reading_list", []),
                "quiz": result.get("quiz", []),
                "produced_at": datetime.now(timezone.utc).isoformat(),
            },
            "message": (
                f"Training materials ready — {mod_count} module(s), "
                f"{len(result.get('exercises', []))} exercise(s), "
                f"{quiz_count} quiz question(s)."
            ),
        }

    except Exception as exc:
        logger.exception("[kio13] failed: {}", exc)
        return {
            "status": "DONE",
            "artifact_id": str(uuid.uuid4()),
            "artifact_data": {
                "kio": KIO_ID,
                "error": str(exc),
                "modules": [],
                "produced_at": datetime.now(timezone.utc).isoformat(),
            },
            "message": f"KIO13 failed: {exc}",
        }


app = make_kio_app(KIO_ID, TITLE, handler)

if __name__ == "__main__":
    cfg = get_settings()
    port = cfg.kio_port_map.get(KIO_ID, 8023)
    uvicorn.run("main:app", host=cfg.api_host, port=port, reload=False)
