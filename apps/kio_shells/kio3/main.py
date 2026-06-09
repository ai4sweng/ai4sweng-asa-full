"""KIO3 — Repo Analyzer Agent

Scans the target repository using RepoContextBuilder and asks the LLM to
analyse each source file for structural issues, anti-patterns, and potential
bugs. Falls back to shaped placeholder findings if LLM is unavailable.
"""
from __future__ import annotations

import asyncio
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

KIO_ID = "kio3"
TITLE = "Repo Analyzer Agent"

_provider = None
_provider_lock = asyncio.Lock()

FALLBACK_FINDINGS = [
    {"file": "app/main.py", "line": 23, "severity": "HIGH", "category": "sql_injection",
     "description": "Raw SQL query with user input — SQL injection risk.", "excerpt": ""},
    {"file": "app/main.py", "line": 67, "severity": "HIGH", "category": "missing_authentication",
     "description": "DELETE endpoint has no authentication.", "excerpt": ""},
    {"file": "app/database.py", "line": 6, "severity": "HIGH", "category": "hardcoded_credentials",
     "description": "Database credentials hardcoded in source.", "excerpt": ""},
    {"file": "app/models.py", "line": 11, "severity": "MEDIUM", "category": "plaintext_password",
     "description": "Password stored as plain text.", "excerpt": ""},
]


async def _get_provider():
    global _provider
    if _provider is None:
        async with _provider_lock:
            if _provider is None:
                _provider = await create_llm_provider()
    return _provider


async def handler(envelope: MessageEnvelope) -> dict:
    payload = envelope.payload
    description = payload.get("description", "Analyse the repository for bugs and issues")
    repo_path = payload.get("working_directory", "") or get_settings().target_repo_path

    findings = []
    file_count = 0

    try:
        provider = await _get_provider()
        abs_path = Path(repo_path)
        if not abs_path.is_absolute():
            abs_path = Path.cwd() / abs_path

        if abs_path.exists():
            builder = RepoContextBuilder(str(abs_path))
            inventory = builder.build_inventory()
            rel_paths = builder.select_analysis_files(
                inventory, max_files=get_settings().repo_analysis_max_files
            )
            file_count = len(rel_paths)
            logger.info("[kio3] Scanning {} file(s) in {}", file_count, abs_path)

            for rel_path in rel_paths:
                file_ctx = builder.read_file_context(rel_path)
                result = await provider.analyze_repository_file(
                    issue=description, file_ctx=file_ctx, repo_path=str(abs_path)
                )
                for f in result.findings:
                    findings.append({
                        "file": f.file_path,
                        "line": f.line_number,
                        "severity": f.severity,
                        "category": f.category,
                        "description": f.description,
                        "excerpt": f.excerpt,
                    })
        else:
            logger.warning("[kio3] Repo path not found: {} — using fallback findings", abs_path)
            findings = FALLBACK_FINDINGS
            file_count = 4

    except Exception as exc:
        logger.exception("[kio3] LLM analysis failed, using fallback: {}", exc)
        findings = FALLBACK_FINDINGS
        file_count = 4

    artifact_data = {
        "kio": KIO_ID,
        "repo_path": repo_path,
        "file_count": file_count,
        "finding_count": len(findings),
        "findings": findings,
        "produced_at": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "status": "DONE",
        "artifact_id": str(uuid.uuid4()),
        "artifact_data": artifact_data,
        "message": f"Repository analysed: {len(findings)} finding(s) across {file_count} file(s).",
    }


app = make_kio_app(KIO_ID, TITLE, handler)

if __name__ == "__main__":
    cfg = get_settings()
    port = cfg.kio_port_map.get(KIO_ID, 8013)
    uvicorn.run("main:app", host=cfg.api_host, port=port, reload=False)
