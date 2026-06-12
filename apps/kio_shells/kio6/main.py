"""KIO6 — Patch Agent

Reads the confirmed bug list from kio5 (via last_artifact) and the
original repository, then generates corrected source files for each bug.

Uses markdown code-block output (not JSON) to work reliably with small LLMs.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
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
from shared.tools.repo_context_builder import RepoContextBuilder
from shared.config import get_settings

KIO_ID = "kio6"
TITLE = "Patch Agent"

SYSTEM_PROMPT = """\
You are an expert software engineer. Given a list of confirmed bugs and the
relevant source files, generate corrected versions of each affected file.

Rules:
- Fix ONLY the reported bugs — do not refactor unrelated code
- Preserve all existing logic that is not buggy
- Add a short comment on each changed line: # FIX <BUG-ID>: <reason>
- If a bug requires a new import, add it at the top of the file
- CRITICAL: Preserve the EXACT same web framework as the original (FastAPI stays FastAPI, Flask stays Flask — do NOT switch frameworks)
- CRITICAL: Preserve the EXACT same async/sync style as the original (FastAPI with async def stays async def)
- CRITICAL: If the original uses FastAPI, all routes must remain as FastAPI routes with the same decorators and dependency injection style
- CRITICAL: Only add libraries that are standard for the existing stack (FastAPI apps use fastapi, sqlalchemy, passlib, pyjwt — NOT flask)

Output format — list each corrected file as:
### path/to/file.py
```python
<full corrected file content here>
```

Do NOT output JSON. Only output files using the ### heading + code block format above."""


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


def _parse_code_files(text: str) -> list[dict]:
    """Extract files from '### path\\n```lang\\ncode\\n```' blocks."""
    files = []
    pattern = r"###\s+(\S+)\s*\n```(?:\w+)?\n(.*?)```"
    for match in re.finditer(pattern, text, re.DOTALL):
        path = match.group(1).strip()
        content = match.group(2).strip()
        if path and content:
            files.append({"path": path, "content": content})
    return files


_REPO_CANDIDATES = [
    "/examples/buggy_fastapi_repo",
    "/repos/target",
]


def _resolve_repo(working_directory: str) -> str | None:
    """Find the repo on the filesystem — mirrors kio7's logic."""
    candidates = []
    if working_directory:
        rel = working_directory.lstrip("/")
        candidates += [
            f"/examples/{rel}",
            f"/repos/{rel}",
            working_directory,
        ]
    candidates += _REPO_CANDIDATES
    for path in candidates:
        if os.path.isdir(path):
            return path
    return None


async def _read_buggy_files(repo_path: str, bugs: list[dict]) -> str:
    resolved = _resolve_repo(repo_path)
    if not resolved:
        logger.warning(
            "[kio6] Repo not found for path '{}' — LLM will patch without source context", repo_path
        )
        return ""
    logger.info("[kio6] Reading source from: {}", resolved)
    bug_files = {b.get("file", "") for b in bugs if b.get("file")}
    builder = RepoContextBuilder(resolved)
    inventory = builder.build_inventory()
    rel_paths = builder.select_analysis_files(
        inventory, max_files=get_settings().repo_analysis_max_files
    )
    parts = []
    for rel_path in rel_paths:
        if (
            any(rel_path.endswith(bf.lstrip("/")) or bf.endswith(rel_path) for bf in bug_files)
            or not bug_files
        ):
            ctx = builder.read_file_context(rel_path)
            parts.append(f"### {rel_path}\n```python\n{ctx.excerpt}\n```")
    return "\n\n".join(parts) if parts else ""


async def handler(envelope: MessageEnvelope) -> dict:
    payload = envelope.payload
    description = payload.get("description", "")
    feedback = payload.get("feedback", "")
    repo_path = payload.get("working_directory", "") or get_settings().target_repo_path
    last_artifact = payload.get("last_artifact", {})
    bugs = last_artifact.get("bugs", [])
    initial_context = payload.get("initial_context", {})
    inline_code = initial_context.get("code", "")

    logger.info("[kio6] Patching {} confirmed bug(s). Feedback: '{}'", len(bugs), feedback[:80])

    _js = None
    try:
        from shared.messaging.jetstream import get_jetstream

        _js = await get_jetstream()
    except Exception:
        pass

    await publish_progress(
        KIO_ID, envelope.session_id, 10, f"Reading source files for {len(bugs)} bug(s)…", _js
    )

    patches = []
    summary = ""

    try:
        llm_override = payload.get("llm_provider_override", "")
        provider = await _get_provider(llm_override)
        source_context = await _read_buggy_files(repo_path, bugs)

        # Code-snippet mode: use the submitted code as source when no repo is on disk
        if not source_context and inline_code:
            source_context = f"### app.py\n```python\n{inline_code}\n```"
            logger.info("[kio6] No repo — using inline code snippet as source context ({} chars)", len(inline_code))

        bugs_text = json.dumps(bugs, indent=2)

        user_prompt = (
            f"Task: {description}\n"
            + (f"Human feedback: {feedback}\n" if feedback else "")
            + f"\nConfirmed bugs to fix:\n{bugs_text}\n\n"
            f"Source files containing the bugs:\n"
            f"{source_context or '(repo not accessible)'}\n\n"
            "Generate corrected files using the ### heading + code block format."
        )

        await publish_progress(
            KIO_ID,
            envelope.session_id,
            40,
            "Sending bugs + source to LLM for patch generation…",
            _js,
        )
        response = await provider.complete(user_prompt, system=SYSTEM_PROMPT)
        await publish_progress(KIO_ID, envelope.session_id, 75, "Parsing patches…", _js)
        patches = _parse_code_files(response.content)

        if not patches:
            logger.warning("[kio6] No ### blocks found in response, trying fence-only extraction")
            # Last resort: find any ```python ... ``` blocks and guess filenames from bugs
            code_blocks = re.findall(r"```python\n(.*?)```", response.content, re.DOTALL)
            bug_files = list({b.get("file", "") for b in bugs if b.get("file")})
            for i, block in enumerate(code_blocks):
                path = bug_files[i] if i < len(bug_files) else f"patched_file_{i}.py"
                patches.append({"path": path, "content": block.strip()})

        summary = f"Patched {len(patches)} file(s) addressing {len(bugs)} bug(s)"
        logger.info("[kio6] {}", summary)

    except Exception as exc:
        logger.exception("[kio6] LLM patch generation failed: {}", exc)
        summary = f"Patch generation failed: {exc}"

    artifact_data = {
        "kio": KIO_ID,
        "summary": summary,
        "patch_count": len(patches),
        "bugs_addressed": [b.get("bug_id") for b in bugs],
        "patches": patches,
        # Pass kio4's test files through (received from kio5) so kio7 can use them
        "test_files": last_artifact.get("test_files", []),
        "framework": last_artifact.get("framework", "fastapi"),
        "produced_at": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "status": "DONE",
        "artifact_id": str(uuid.uuid4()),
        "artifact_data": artifact_data,
        "message": f"Patches generated: {len(patches)} file(s) corrected.",
    }


app = make_kio_app(KIO_ID, TITLE, handler)

if __name__ == "__main__":
    cfg = get_settings()
    port = cfg.kio_port_map.get(KIO_ID, 8016)
    uvicorn.run("main:app", host=cfg.api_host, port=port, reload=False)
