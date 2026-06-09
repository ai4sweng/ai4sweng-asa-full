"""KIO1 — Prompt Router

Receives a natural-language prompt (and optional code snippet) and decides
which KIO pipeline to execute.  Returns a `kio_sequence` that the orchestrator
injects into the running graph via the existing dynamic-pipeline mechanism.

Supported intents (auto-detected from the prompt language):
  bug_detect        → ["kio1", "kio5"]
  bug_and_patch     → ["kio1", "kio5", "kio6", "kio7"]
  test_gen          → ["kio1", "kio4"]
  analyze           → ["kio1", "kio3", "kio5"]
  full_pipeline     → ["kio1", "kio3", "kio4", "kio5", "kio6", "kio7", "kio8"]
  report_only       → ["kio1", "kio8"]
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

from kio_base import MessageEnvelope, make_kio_app
from shared.llm.factory import create_llm_provider
from shared.config import get_settings

KIO_ID = "kio1"
TITLE = "Prompt Router"

SYSTEM_PROMPT = """\
You are a pipeline router for an AI engineering platform.
Given a user request and optional code snippet, decide which processing pipeline to run.

Available KIOs (in logical order):
- kio9: Code Generator — generates code from a description (use when no code is provided and code must be written)
- kio3: Repository Analyzer — reads and analyzes an entire codebase from disk
- kio4: Test Generator — generates pytest tests for code
- kio5: Bug Detector — finds security/logic bugs (works with direct code OR upstream findings)
- kio6: Patch Generator — generates code fixes for confirmed bugs
- kio7: Test Re-runner — runs tests after patching to verify fixes
- kio8: Report Generator — produces a final analysis report

Routing rules:
- "kod yaz / generate code / write code / create" with no code provided → ["kio1","kio9","kio5"]
- "kod yaz ... ve bug bul / hata yap" with no code → ["kio1","kio9","kio5"]
- Code is provided + "bug / vulnerability / güvenlik / hata" → ["kio1","kio5"]
- Code is provided + "fix / patch / düzelt" → ["kio1","kio5","kio6","kio7"]
- "test yaz / test generate" → ["kio1","kio4"]
- "analyze / incele / analiz et" → ["kio1","kio3","kio5"]
- "full / complete / hepsi / tüm pipeline" → ["kio1","kio3","kio4","kio5","kio6","kio7","kio8"]
- "report / rapor" alone → ["kio1","kio8"]
- Default when unsure: ["kio1","kio5"]

ALWAYS include "kio1" as the first element.
Include "kio7" whenever "kio6" is included (verify patches work).
Set hitl_after to ["kio5"] whenever kio5 is in the sequence.

Return ONLY valid JSON, no markdown:
{
  "kio_sequence": ["kio1", "kio5"],
  "hitl_after": ["kio5"],
  "description": "one-line description of what the pipeline will do",
  "reasoning": "why you chose this pipeline"
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


_FALLBACK_SEQUENCE = ["kio1", "kio5"]
_FALLBACK_HITL = ["kio5"]

_KNOWN_SEQUENCES = {
    "bug_detect":    (["kio1", "kio5"],                          ["kio5"]),
    "bug_and_patch": (["kio1", "kio5", "kio6", "kio7"],          ["kio5"]),
    "test_gen":      (["kio1", "kio4"],                          []),
    "analyze":       (["kio1", "kio3", "kio5"],                  ["kio5"]),
    "full_pipeline": (["kio1", "kio3", "kio4", "kio5", "kio6", "kio7", "kio8"], ["kio5"]),
    "report_only":   (["kio1", "kio8"],                          []),
}


async def handler(envelope: MessageEnvelope) -> dict:
    payload = envelope.payload
    description = payload.get("description", "")
    initial_context = payload.get("initial_context", {})
    code = initial_context.get("code", "")

    logger.info("[kio1] Routing prompt: {}", description[:120])

    kio_sequence = _FALLBACK_SEQUENCE
    hitl_after = _FALLBACK_HITL
    routing_description = description
    reasoning = "Default route"

    try:
        llm_override = payload.get("llm_provider_override", "")
        provider = await _get_provider(llm_override)

        code_section = f"\n\nProvided code:\n```\n{code[:3000]}\n```" if code else "\n\n(No code provided)"
        user_prompt = f"User request: {description}{code_section}"

        response = await provider.complete(user_prompt, system=SYSTEM_PROMPT)
        result = json.loads(_strip_fences(response.content))

        raw_seq = result.get("kio_sequence", [])
        if (
            isinstance(raw_seq, list)
            and len(raw_seq) >= 1
            and raw_seq[0] == "kio1"
            and all(isinstance(k, str) for k in raw_seq)
        ):
            kio_sequence = raw_seq
            hitl_after = result.get("hitl_after", [])
            routing_description = result.get("description", description)
            reasoning = result.get("reasoning", "")
        else:
            logger.warning("[kio1] LLM returned invalid sequence {}, using fallback", raw_seq)

    except Exception as exc:
        logger.warning("[kio1] LLM routing failed ({}), using fallback", exc)

    logger.info("[kio1] Route: {} hitl_after={}", kio_sequence, hitl_after)

    artifact_data = {
        "kio": KIO_ID,
        "kio_sequence": kio_sequence,
        "hitl_after": hitl_after,
        "intent_description": routing_description,
        "reasoning": reasoning,
        "code": code,
        "prompt": description,
        "findings": [],
        "files": [],
        "produced_at": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "status": "DONE",
        "artifact_id": str(uuid.uuid4()),
        "artifact_data": artifact_data,
        "kio_sequence": kio_sequence,
        "hitl_after": hitl_after,
        "message": f"Routed to: {' → '.join(kio_sequence[1:])}",
    }


app = make_kio_app(KIO_ID, TITLE, handler)

if __name__ == "__main__":
    cfg = get_settings()
    port = cfg.kio_port_map.get(KIO_ID, 8011)
    uvicorn.run("main:app", host=cfg.api_host, port=port, reload=False)
