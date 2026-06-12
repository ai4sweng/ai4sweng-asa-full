"""KIO11 — AI-Powered Test Automation Tool (TAT)

Reads the repository source and generates comprehensive pytest test suites,
fixtures, and coverage configuration using AI-driven test case synthesis.
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

KIO_ID = "kio11"
TITLE = "AI-Powered Test Automation Tool (TAT)"

SYSTEM_PROMPT = """\
You are an expert test engineer specialising in AI-driven test automation.

Given source code you generate:
1. Comprehensive pytest test suites covering: unit tests for every public \
   function/method, integration tests for module interactions, and edge-case \
   tests for boundary conditions and error paths.
2. A conftest.py with shared fixtures if multiple test files need common setup.
3. A pytest.ini (or pyproject.toml [tool.pytest.ini_options] section) \
   with coverage thresholds set to 80 %+.
4. Where behaviour is non-trivial, add hypothesis @given strategies for \
   property-based testing.

Naming: test files go in a tests/ directory and are named test_<module>.py.

Return ONLY a valid JSON object — no markdown fences, no prose outside JSON:
{
  "summary": "what was tested and key design decisions",
  "test_count": 12,
  "files": [
    {"path": "tests/test_example.py", "content": "...full file content..."}
  ]
}"""


_provider = None
_provider_lock = asyncio.Lock()


async def _get_provider():
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
    rel_paths = builder.select_analysis_files(inventory, max_files=cfg.repo_analysis_max_files)
    parts = []
    for rel_path in rel_paths:
        ctx = builder.read_file_context(rel_path)
        parts.append(f"## {rel_path}\n```\n{ctx.excerpt}\n```")
    return "\n\n".join(parts)


async def handler(envelope: MessageEnvelope) -> dict:
    payload = envelope.payload
    description = payload.get("description", "Generate test suites for the repository")
    repo_path = payload.get("working_directory", "") or get_settings().target_repo_path

    try:
        provider = await _get_provider()
        repo_context = await _build_repo_context(repo_path)

        user_prompt = (
            f"Testing objective: {description}\n\n"
            f"Source code to test:\n{repo_context or '(no repository provided)'}\n\n"
            "Generate comprehensive pytest suites, fixtures, and config as JSON."
        )

        response = await provider.complete(user_prompt, system=SYSTEM_PROMPT)
        raw = _strip_fences(response.content)

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = {"summary": raw, "test_count": 0, "files": []}

        files = result.get("files", [])
        test_count = result.get("test_count", len(files))
        logger.info("[kio11] Generated {} test file(s)", len(files))
        return {
            "status": "DONE",
            "artifact_id": str(uuid.uuid4()),
            "artifact_data": {
                "kio": KIO_ID,
                "summary": result.get("summary", ""),
                "test_count": test_count,
                "files": files,
                "produced_at": datetime.now(timezone.utc).isoformat(),
            },
            "message": f"Test automation complete — {len(files)} file(s), ~{test_count} test(s).",
        }

    except Exception as exc:
        logger.exception("[kio11] failed: {}", exc)
        return {
            "status": "DONE",
            "artifact_id": str(uuid.uuid4()),
            "artifact_data": {
                "kio": KIO_ID,
                "error": str(exc),
                "files": [],
                "produced_at": datetime.now(timezone.utc).isoformat(),
            },
            "message": f"KIO11 failed: {exc}",
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
    port = cfg.kio_port_map.get(KIO_ID, 8021)
    uvicorn.run("main:app", host=cfg.api_host, port=port, reload=False)
