"""KIO4 — Test Generator Agent

Reads kio3 findings from last_artifact and generates a pytest test suite
covering each identified issue. Passes findings through in its own artifact
so kio5 can access them without needing the full artifact chain.

Uses markdown code-block output (not JSON) to work reliably with small LLMs.
"""

from __future__ import annotations

import asyncio
import json
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
from shared.config import get_settings

KIO_ID = "kio4"
TITLE = "Test Generator Agent"

_SYSTEM_PROMPT_FASTAPI = """\
You are an expert test engineer. Given a list of code findings (potential bugs) in a FastAPI app, generate EXACTLY TWO pytest test files that verify the CORRECT/SECURE expected behavior.

CRITICAL RULES:
- File 1: tests/test_security.py — pytest test functions named test_<something>()
- File 2: tests/conftest.py — pytest fixtures using FastAPI TestClient
- test functions MUST start with "def test_"
- Import from app.main: from app.main import app
- Do NOT generate application code — only test code

ASSERTION RULES:
- ALWAYS assert what SHOULD happen after the bug is fixed (the CORRECT behavior), never the buggy behavior
- For SQL injection tests: assert status codes in (400, 422, 404, 405)
- For unauthenticated DELETE/PUT/PATCH: assert status in (401, 403)

Example output:

### tests/conftest.py
```python
import pytest
from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
```

### tests/test_security.py
```python
import pytest

def test_sql_injection_blocked(client):
    response = client.get("/users/1 OR 1=1")
    assert response.status_code in (400, 422, 404, 405)

def test_delete_requires_auth(client):
    response = client.delete("/users/1")
    assert response.status_code in (401, 403)
```

Only output the ### heading + python code block pairs above. No explanations."""

_SYSTEM_PROMPT_FLASK = """\
You are an expert test engineer. Given a list of code findings (potential bugs) in a Flask app, generate EXACTLY TWO pytest test files that verify the CORRECT/SECURE expected behavior.

CRITICAL RULES:
- File 1: tests/test_security.py — pytest test functions named test_<something>()
- File 2: tests/conftest.py — pytest fixtures using Flask's built-in test_client()
- test functions MUST start with "def test_"
- Import the Flask app object: from app import app  (the app variable from app.py)
- Do NOT use FastAPI, TestClient, or SQLAlchemy — the app uses Flask and sqlite3
- Do NOT generate application code — only test code
- Use response.get_json() (not response.json()) for Flask responses
- Use response.status_code to check status

ASSERTION RULES:
- ALWAYS assert what SHOULD happen after the bug is fixed (the CORRECT behavior), never the buggy behavior
- For SQL injection tests: send a payload with SQL metacharacters; assert the response does NOT return data it shouldn't (status 200 with wrong data, or status in (400, 401, 404, 500))
- For unauthenticated endpoints: assert status in (401, 403)
- Keep tests simple — test one thing per function

Example output:

### tests/conftest.py
```python
import pytest
from app import app as flask_app

@pytest.fixture(scope="module")
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c
```

### tests/test_security.py
```python
import pytest

def test_login_sql_injection_rejected(client):
    response = client.post("/login", json={"username": "' OR '1'='1", "password": "x"})
    assert response.status_code in (401, 400, 500)

def test_admin_users_requires_auth(client):
    response = client.get("/admin/users")
    assert response.status_code in (401, 403)

def test_search_sql_injection_safe(client):
    response = client.get("/search?q=' OR '1'='1")
    assert response.status_code in (200, 400, 500)
```

Only output the ### heading + python code block pairs above. No explanations."""


def _detect_framework(code: str) -> str:
    """Return 'flask' or 'fastapi' based on import statements in the code."""
    code_lower = code.lower()
    if "from flask" in code_lower or "import flask" in code_lower:
        return "flask"
    if "from fastapi" in code_lower or "import fastapi" in code_lower:
        return "fastapi"
    # Default to fastapi for repo-based pipelines with no inline code
    return "fastapi"


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


async def handler(envelope: MessageEnvelope) -> dict:
    payload = envelope.payload
    description = payload.get("description", "")
    last_artifact = payload.get("last_artifact", {})
    findings = last_artifact.get("findings", [])
    initial_context = payload.get("initial_context", {})
    inline_code = initial_context.get("code", "")

    framework = _detect_framework(inline_code)
    system_prompt = _SYSTEM_PROMPT_FLASK if framework == "flask" else _SYSTEM_PROMPT_FASTAPI
    logger.info("[kio4] Generating tests for {} finding(s) — framework={}", len(findings), framework)

    # Resolve NATS JS for publish_progress (None = HTTP mode, progress still logged)
    _js = None
    try:
        from shared.messaging.jetstream import get_jetstream

        _js = await get_jetstream()
    except Exception:
        pass

    await publish_progress(
        KIO_ID,
        envelope.session_id,
        10,
        f"Preparing test generation for {len(findings)} finding(s)",
        _js,
    )

    files = []
    summary = f"Test generation for {len(findings)} finding(s)"

    try:
        llm_override = payload.get("llm_provider_override", "")
        provider = await _get_provider(llm_override)
        findings_text = json.dumps(findings, indent=2) if findings else "[]"
        user_prompt = (
            f"Task: {description}\n\n"
            f"Findings to cover with tests:\n{findings_text}\n\n"
            "Generate pytest test files using the ### heading + code block format."
        )

        await publish_progress(KIO_ID, envelope.session_id, 40, "Sending prompt to LLM…", _js)
        response = await provider.complete(user_prompt, system=system_prompt)
        await publish_progress(
            KIO_ID, envelope.session_id, 70, "Parsing generated test files…", _js
        )
        files = _parse_code_files(response.content)

        if not files:
            # Fallback: treat entire response as a single test file if it contains def test_
            content = response.content.strip()
            if "def test_" in content:
                # Strip any leading/trailing fences
                if content.startswith("```"):
                    content = "\n".join(content.split("\n")[1:])
                if content.endswith("```"):
                    content = content[: content.rfind("```")].strip()
                files = [{"path": "tests/test_security.py", "content": content}]

        test_count = sum(
            content.count("def test_") for f in files for content in [f.get("content", "")]
        )
        summary = f"Generated {len(files)} test file(s) with ~{test_count} test(s)"
        logger.info("[kio4] {}", summary)
        await publish_progress(KIO_ID, envelope.session_id, 90, summary, _js)

    except Exception as exc:
        logger.exception("[kio4] LLM test generation failed: {}", exc)
        summary = f"Test generation failed: {exc}"

    artifact_data = {
        "kio": KIO_ID,
        "summary": summary,
        "framework": framework,
        "test_count": sum(f.get("content", "").count("def test_") for f in files),
        "file_count": len(files),
        "test_files": files,  # canonical key used by kio5→kio6→kio7
        "files": files,       # backward-compat alias
        # Pass-through so kio5 can access kio3 findings
        "findings": findings,
        "produced_at": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "status": "DONE",
        "artifact_id": str(uuid.uuid4()),
        "artifact_data": artifact_data,
        "message": f"Generated {len(files)} test file(s).",
    }


app = make_kio_app(KIO_ID, TITLE, handler)

if __name__ == "__main__":
    cfg = get_settings()
    port = cfg.kio_port_map.get(KIO_ID, 8014)
    uvicorn.run("main:app", host=cfg.api_host, port=port, reload=False)
