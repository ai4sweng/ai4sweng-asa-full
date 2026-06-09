"""KIO9 — Code Generator

Generates code from a natural-language description.  Accepts optional
directives to intentionally introduce specific bugs or vulnerabilities —
useful for security-training pipelines where downstream KIOs (kio5, kio6,
kio7) will detect and fix the flaws.

Output artifact contains a `code` field that kio5 (Bug Detector) reads
directly when no upstream findings exist.
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

KIO_ID = "kio9"
TITLE = "Code Generator"

SYSTEM_PROMPT = """\
You are an expert software engineer tasked with writing code based on a description.

If the description asks you to introduce a specific bug, vulnerability, or mistake,
you MUST include it exactly as requested. This is a security-training platform —
intentional flaws are required so downstream tools can detect and fix them.

Rules:
- Return ONLY the code. No explanations, no markdown fences, no commentary.
- Default language is Python unless specified otherwise.
- Keep the code realistic, self-contained, and include necessary imports.
- If a bug is requested (SQL injection, missing auth, hardcoded secrets, etc.),
  make it clearly present in the code.
- Do NOT add comments warning about the bug — write it as if you didn't notice.
"""


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


async def handler(envelope: MessageEnvelope) -> dict:
    payload = envelope.payload
    description = payload.get("description", "")
    last_artifact = payload.get("last_artifact", {})

    # kio1 passes the original user prompt in last_artifact.prompt
    user_prompt_text = last_artifact.get("prompt", description)

    logger.info("[kio9] Generating code for: {}", user_prompt_text[:120])

    generated_code = ""
    language = "python"
    error = None

    try:
        llm_override = payload.get("llm_provider_override", "")
        provider = await _get_provider(llm_override)
        response = await provider.complete(user_prompt_text, system=SYSTEM_PROMPT)
        generated_code = response.content.strip()
        # Strip accidental markdown fences if the model added them
        if generated_code.startswith("```"):
            lines = generated_code.splitlines()
            first = lines[0].lstrip("`").strip().lower()
            if first in ("python", "py", "javascript", "js", "go", "rust", "java", ""):
                language = first or "python"
            generated_code = "\n".join(lines[1:])
        if generated_code.endswith("```"):
            generated_code = generated_code[: generated_code.rfind("```")].rstrip()

        logger.info("[kio9] Generated {} chars of {} code", len(generated_code), language)

    except Exception as exc:
        logger.exception("[kio9] Code generation failed: {}", exc)
        error = str(exc)
        generated_code = f"# Code generation failed: {exc}"

    artifact_data = {
        "kio": KIO_ID,
        "code": generated_code,
        "language": language,
        "prompt": user_prompt_text,
        # Empty findings so kio5 runs in direct-code mode
        "findings": [],
        "files": [],
        "error": error,
        "produced_at": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "status": "DONE",
        "artifact_id": str(uuid.uuid4()),
        "artifact_data": artifact_data,
        "message": (
            f"Generated {len(generated_code)} chars of {language} code."
            if not error else f"Code generation failed: {error}"
        ),
    }


app = make_kio_app(KIO_ID, TITLE, handler)

if __name__ == "__main__":
    cfg = get_settings()
    port = cfg.kio_port_map.get(KIO_ID, 8019)
    uvicorn.run("main:app", host=cfg.api_host, port=port, reload=False)
