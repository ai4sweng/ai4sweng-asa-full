"""KIO12 — AI-Assisted Cybersecurity & Multi-Level Security

Scans source code for vulnerabilities (OWASP Top 10, injection, broken auth,
MLS policy gaps) and generates hardened replacement files with security
controls, input validation, and audit logging applied.
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
from shared.tools.repo_context_builder import RepoContextBuilder
from shared.config import get_settings

KIO_ID = "kio12"
TITLE = "AI-Assisted Cybersecurity & Multi-Level Security"

SYSTEM_PROMPT = """\
You are a cybersecurity expert specialising in secure code generation and \
multi-level security (MLS) policy enforcement.

Given source code you:
1. Identify vulnerabilities mapped to OWASP Top 10: injection (SQL, command, \
   SSTI), broken authentication, sensitive data exposure, security \
   misconfiguration, XSS, insecure deserialisation, known CVEs in deps.
2. Identify MLS policy gaps: missing access-control labels, privilege \
   escalation paths, covert channels.
3. Generate security-hardened replacement files that apply: parameterised \
   queries, input validation/sanitisation, proper auth & authorisation \
   middleware, secrets management (no hardcoded credentials), structured \
   audit logging for security-relevant events.

Return ONLY a valid JSON object — no markdown fences, no prose outside JSON:
{
  "summary": "overall security posture and key remediations applied",
  "vulnerabilities": [
    {"file": "path/to/file.py", "line": 10, "cwe": "CWE-89", "description": "..."}
  ],
  "files": [
    {"path": "path/to/file.py", "content": "...full hardened file content..."}
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


async def _build_repo_context(repo_path: str) -> str:
    abs_path = Path(repo_path)
    if not abs_path.is_absolute():
        abs_path = Path.cwd() / abs_path
    if not abs_path.exists():
        return ""
    cfg = get_settings()
    builder = RepoContextBuilder(str(abs_path))
    inventory = builder.build_inventory()
    rel_paths = builder.select_analysis_files(
        inventory, max_files=cfg.repo_analysis_max_files
    )
    parts = []
    for rel_path in rel_paths:
        ctx = builder.read_file_context(rel_path)
        parts.append(f"## {rel_path}\n```\n{ctx.content}\n```")
    return "\n\n".join(parts)


async def handler(envelope: MessageEnvelope) -> dict:
    payload = envelope.payload
    description = payload.get("description", "Audit and harden the repository for security")
    repo_path = payload.get("working_directory", "") or get_settings().target_repo_path
    # A2A callers (e.g. kio5) may pass inline code instead of a repo path
    inline_code = payload.get("code", "")

    try:
        llm_override = payload.get("llm_provider_override", "")
        provider = await _get_provider(llm_override)

        if inline_code:
            repo_context = f"## inline_code.py\n```\n{inline_code[:6000]}\n```"
            logger.info("[kio12] Using inline code ({} chars) — skipping repo scan", len(inline_code))
        else:
            repo_context = await _build_repo_context(repo_path)

        user_prompt = (
            f"Security objective: {description}\n\n"
            f"Source code to audit and harden:\n{repo_context or '(no repository provided)'}\n\n"
            "Identify all vulnerabilities and generate hardened replacement files as JSON."
        )

        response = await provider.complete(user_prompt, system=SYSTEM_PROMPT)
        raw = _strip_fences(response.content)

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = {"summary": raw, "vulnerabilities": [], "files": []}

        vulns = result.get("vulnerabilities", [])
        files = result.get("files", [])
        logger.info("[kio12] Found {} vulnerability/ies, generated {} hardened file(s)", len(vulns), len(files))
        return {
            "status": "DONE",
            "artifact_id": str(uuid.uuid4()),
            "artifact_data": {
                "kio": KIO_ID,
                "summary": result.get("summary", ""),
                "vulnerability_count": len(vulns),
                "vulnerabilities": vulns,
                "files": files,
                "produced_at": datetime.now(timezone.utc).isoformat(),
            },
            "message": (
                f"Security audit complete — {len(vulns)} vulnerability/ies found, "
                f"{len(files)} file(s) hardened."
            ),
        }

    except Exception as exc:
        logger.exception("[kio12] failed: {}", exc)
        return {
            "status": "DONE",
            "artifact_id": str(uuid.uuid4()),
            "artifact_data": {
                "kio": KIO_ID,
                "error": str(exc),
                "files": [],
                "produced_at": datetime.now(timezone.utc).isoformat(),
            },
            "message": f"KIO12 failed: {exc}",
        }


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = text[: text.rfind("```")].strip()
    return text


app = make_kio_app(KIO_ID, TITLE, handler)

if __name__ == "__main__":
    cfg = get_settings()
    port = cfg.kio_port_map.get(KIO_ID, 8022)
    uvicorn.run("main:app", host=cfg.api_host, port=port, reload=False)
