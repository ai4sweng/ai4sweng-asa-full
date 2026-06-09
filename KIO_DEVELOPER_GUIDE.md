# KIO Shell Developer Guide

**Platform:** KIO1 AI Engineering Platform  
**Audience:** Teams implementing KIO2 – KIO13  
**Version:** Phase 10 (Orchestrator SM + Timeout Monitor + NATS Pull Consumer + publish_progress)

---

## Table of Contents

1. [What is a KIO Shell?](#1-what-is-a-kio-shell)
2. [How Your KIO Fits Into the Platform](#2-how-your-kio-fits-into-the-platform)
3. [Prerequisites](#3-prerequisites)
4. [Project Structure](#4-project-structure)
5. [Implementing Your Handler (The Only File You Touch)](#5-implementing-your-handler-the-only-file-you-touch)
6. [Input: The MessageEnvelope](#6-input-the-messageenvelope)
7. [Reporting Intermediate Progress](#7-reporting-intermediate-progress)
8. [Output: The Result Dict Contract](#8-output-the-result-dict-contract)
9. [Status Codes: DONE vs REVIEW_REQUIRED](#9-status-codes-done-vs-review_required)
10. [Using the LLM](#10-using-the-llm)
11. [Calling Another KIO (A2A)](#11-calling-another-kio-a2a)
12. [Using MCP Tools (Filesystem, Shell)](#12-using-mcp-tools-filesystem-shell)
13. [Triggering HITL from Your KIO](#13-triggering-hitl-from-your-kio)
14. [Configuration & Environment Variables](#14-configuration--environment-variables)
15. [Running Locally (Without Docker)](#15-running-locally-without-docker)
16. [Running With Docker Compose](#16-running-with-docker-compose)
17. [Testing Your KIO](#17-testing-your-kio)
18. [Handoff Checklist](#18-handoff-checklist)
19. [Common Mistakes](#19-common-mistakes)

---

## 1. What is a KIO Shell?

A **KIO (Knowledge Integration Operation)** is a single-responsibility microservice that performs one AI-assisted step in a software-engineering workflow. Each KIO:

- Receives a **JOB_REQUEST** envelope (from the orchestrator via NATS or HTTP)
- Does its work (LLM calls, file operations, test runs, etc.)
- Returns a **JOB_RESULT** with a structured artifact
- Optionally requests a **Human-in-the-Loop (HITL)** review before the workflow continues

The platform handles all routing, persistence, retries, and HITL pausing for you. Your job is to write **one async Python function** inside your KIO's `main.py`.

---

## 2. How Your KIO Fits Into the Platform

```
User (dashboard / API)
        │
        ▼
  Orchestrator (LangGraph)
        │  ← picks KIO sequence via LM Engine, or uses the user's explicit list
        ▼
  ┌─────────────────────────────────────────────────┐
  │  KIO2 → KIO3 → KIO4 → KIO5 → ... → KIO13       │
  │  each step produces an artifact in PostgreSQL    │
  │  HITL can pause between any two steps            │
  └─────────────────────────────────────────────────┘
        │
        ▼
  Session Manager (PostgreSQL)  ←  stores all artifacts + checkpoints
```

**Transport:** By default, the orchestrator sends your KIO a request via **NATS JetStream** using a **pull consumer** (durable, at-least-once). Your KIO polls for messages using `fetch(batch=1, timeout=2s)`. If NATS is unavailable, it falls back to **HTTP POST /execute**. Your handler code is the same for both — the platform handles transport selection transparently.

**Capability announcement:** On startup, `make_kio_app()` automatically publishes a `CAPABILITY_ANNOUNCEMENT` message to `kio.{id}.capability` and repeats it every 60 seconds. The orchestrator subscribes to `kio.*.capability` and uses this to discover KIO endpoints dynamically — you don't need to configure KIO locations manually.

**Intermediate progress:** While your handler runs, you can call `publish_progress()` to emit intermediate `TASK_STATUS` events that appear as `TASK_PROGRESS` SSE events in the dashboard. See [Section 7](#7-reporting-intermediate-progress).

---

## 3. Prerequisites

```
Python 3.12+
uv (package manager)
```

For local dev without Docker:
```
ollama running locally (ollama serve)
ollama pull qwen2.5-coder:3b
```

For Docker dev:
```
Docker Desktop 4.x+
docker compose v2
```

Clone and install:
```bash
git clone <repo>
cd EnisAliMerge
uv sync --all-packages    # installs all workspaces including shared
```

---

## 4. Project Structure

```
EnisAliMerge/
├── apps/
│   └── kio_shells/
│       ├── kio_base.py          ← Platform factory. DO NOT EDIT.
│       ├── Dockerfile           ← Shared Dockerfile for all KIOs. DO NOT EDIT.
│       ├── kio2/
│       │   └── main.py          ← YOUR FILE (replace placeholder)
│       ├── kio3/
│       │   └── main.py          ← Real implementation (reference)
│       ├── kio4/
│       │   └── main.py          ← YOUR FILE
│       └── ...
├── shared/
│   ├── config.py                ← All settings / env vars
│   ├── llm/
│   │   └── factory.py           ← get an LLM provider instance
│   ├── a2a/
│   │   └── client.py            ← call another KIO directly
│   └── mcp/
│       ├── registry.py          ← list available tools
│       └── tools/
│           ├── filesystem.py    ← read_file, list_directory, write_file
│           └── shell.py         ← run_command
├── .env.example                 ← copy to .env and fill in
├── docker-compose.yml
└── KIO_DEVELOPER_GUIDE.md       ← this file
```

**You only touch:** `apps/kio_shells/kioN/main.py`

---

## 5. Implementing Your Handler (The Only File You Touch)

### Starting point — what you replace

Every KIO ships as a placeholder:

```python
# apps/kio_shells/kio4/main.py  (current placeholder)
from kio_base import make_kio_app, placeholder_handler
from shared.config import get_settings

KIO_ID = "kio4"
TITLE  = "Test Generation"

app = make_kio_app(KIO_ID, TITLE, placeholder_handler(KIO_ID, "test_generation"))

if __name__ == "__main__":
    cfg = get_settings()
    uvicorn.run("main:app", host=cfg.api_host, port=cfg.kio_port_map[KIO_ID])
```

### Minimal real implementation

```python
# apps/kio_shells/kio4/main.py
"""KIO4 — Test Generation"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Keep these two sys.path lines exactly as-is — needed to find kio_base and shared.
sys.path.insert(0, str(Path(__file__).parents[2]))   # → apps/kio_shells/
sys.path.insert(0, str(Path(__file__).parents[3]))   # → EnisAliMerge/

import uvicorn
from loguru import logger

from kio_base import MessageEnvelope, make_kio_app
from shared.config import get_settings

KIO_ID = "kio4"


async def handler(envelope: MessageEnvelope) -> dict:
    """Your real logic goes here.

    Return a dict — see Section 7 for the exact shape.
    """
    payload     = envelope.payload
    description = payload.get("description", "")
    repo_path   = payload.get("working_directory", "") or get_settings().target_repo_path
    feedback    = payload.get("feedback", "")   # human feedback if this step was resumed after HITL

    logger.info("[{}] Starting — repo={} description={!r}", KIO_ID, repo_path, description[:80])

    # ── Your work here ──────────────────────────────────────────────────────────
    tests_generated = []
    # e.g. read KIO3/KIO5 findings from payload, call LLM, write test files…
    # ────────────────────────────────────────────────────────────────────────────

    return {
        "status":        "DONE",                  # or "REVIEW_REQUIRED"
        "artifact_id":   str(uuid.uuid4()),
        "artifact_data": {
            "kio":            KIO_ID,
            "tests":          tests_generated,
            "produced_at":    datetime.now(timezone.utc).isoformat(),
        },
        "message": f"Generated {len(tests_generated)} tests.",
        # "hitl_question": "...",  ← only needed when status="REVIEW_REQUIRED"
    }


app = make_kio_app(KIO_ID, "Test Generation", handler)

if __name__ == "__main__":
    cfg = get_settings()
    uvicorn.run("main:app", host=cfg.api_host, port=cfg.kio_port_map.get(KIO_ID, 8014), reload=False)
```

---

## 6. Input: The MessageEnvelope

Your handler receives a `MessageEnvelope` Pydantic model. Full field reference:

```python
# Routing / tracing (set by the orchestrator — read-only in your handler)
envelope.message_id        # str — unique message UUID
envelope.correlation_id    # str — UUID shared across all messages in one workflow invocation
envelope.step_id           # str — UUID for this specific pipeline step
envelope.protocol_version  # str — always "1.0.0"
envelope.project_id        # str — from PROJECT_ID env var (e.g. "kio1-platform")
envelope.session_id        # str — identifies the current workflow run
envelope.workflow_id       # str — identifies the workflow definition
envelope.source            # str — sender (orchestrator's project_id)
envelope.target            # str — your KIO ID (e.g. "kio4")
envelope.timestamp         # str — ISO-8601 UTC timestamp
envelope.message_type      # str — always "JOB_REQUEST"

# Your data
envelope.payload           # dict — everything the orchestrator put in for you
```

### Standard payload keys

The orchestrator always sets these in `envelope.payload`:

| Key | Type | Description |
|---|---|---|
| `description` | `str` | User's task description ("Find SQL injection bugs") |
| `working_directory` | `str` | Absolute path of the repo being processed |
| `feedback` | `str` | Human feedback if the workflow was paused at HITL before this step |
| `timeout_seconds` | `int\|None` | Per-task timeout if the caller set one; `None` means use the platform default |

### How to pass data between KIOs

The orchestrator does **not** automatically forward one KIO's output as the next KIO's input. Two patterns are used:

**Pattern A — Direct payload (simple):** The workflow runner passes the same `description` and `working_directory` to every KIO. Each KIO fetches what it needs from the Session Manager or reads from the filesystem.

**Pattern B — Artifact lookup:** If your KIO needs the structured output of a previous KIO (e.g. KIO4 needs KIO3's findings), query the Session Manager:

```python
import httpx
from shared.config import get_settings

async def _get_previous_findings(session_id: str) -> list[dict]:
    cfg = get_settings()
    async with httpx.AsyncClient(base_url=cfg.session_manager_url) as client:
        resp = await client.get(f"/sessions/{session_id}/artifacts")
        resp.raise_for_status()
        artifacts = resp.json()
    # pick the kio3 artifact
    for art in artifacts:
        if art.get("producer_kio") == "kio3":
            return art.get("artifact_data", {}).get("findings", [])
    return []
```

---

## 7. Reporting Intermediate Progress

While your handler is doing long-running work (scanning many files, multiple LLM calls, etc.), call `publish_progress()` to send live progress updates to the dashboard. The orchestrator subscribes to `kio.*.status` and re-emits each update as a `TASK_PROGRESS` SSE event.

```python
from kio_base import publish_progress

async def handler(envelope: MessageEnvelope) -> dict:
    js = None  # publish_progress is a no-op when js is None (HTTP-only mode)
    try:
        from shared.messaging.jetstream import get_jetstream
        js = await get_jetstream()
    except Exception:
        pass

    await publish_progress(KIO_ID, envelope.session_id, 10, "Starting analysis…", js)

    # do work …
    for i, file in enumerate(files):
        # process file…
        pct = int(10 + 80 * (i + 1) / len(files))
        await publish_progress(KIO_ID, envelope.session_id, pct, f"Scanned {i+1}/{len(files)} files", js)

    await publish_progress(KIO_ID, envelope.session_id, 100, "Done", js)
    return { "status": "DONE", … }
```

`publish_progress(kio_id, session_id, progress_pct, message, js)`:
- `progress_pct` is clamped to `[0, 100]`
- `js` must be a connected `JetStreamManager`; if `None` the call is silently skipped
- Publishes to `kio.{kio_id}.status` with `message_type="TASK_STATUS"` and `status="RUNNING"`
- Dashboard shows each update as a `TASK_PROGRESS` event line

---

## 8. Output: The Result Dict Contract

Your handler **must** return a `dict` with these keys:

```python
{
    # REQUIRED
    "status":        str,   # "DONE" or "REVIEW_REQUIRED"
    "artifact_id":   str,   # uuid4 — a unique ID for what you produced
    "artifact_data": dict,  # your structured output (stored in PostgreSQL)
    "message":       str,   # human-readable one-liner shown in the dashboard

    # REQUIRED only when status == "REVIEW_REQUIRED"
    "hitl_question": str,   # question shown to the human approver
}
```

### `artifact_data` structure

There is no enforced schema — put whatever is useful. Conventions used by the existing KIOs:

```python
"artifact_data": {
    "kio":         "kio4",                        # always include your KIO_ID
    "produced_at": datetime.now(timezone.utc).isoformat(),  # always include timestamp
    # … your payload
    "tests": [...],
    "bug_count": 3,
    "patch_files": ["path/to/fix.py"],
}
```

The whole dict is stored verbatim in PostgreSQL. The dashboard and downstream KIOs can retrieve it via the Session Manager API.

---

## 9. Status Codes: DONE vs REVIEW_REQUIRED

| Value | Meaning | Orchestrator reaction |
|---|---|---|
| `"DONE"` | Completed successfully, continue to next KIO | Advances immediately |
| `"REVIEW_REQUIRED"` | A human should inspect the output before continuing | Pauses workflow, sends SSE HITL_CHECKPOINT event to dashboard, waits for approval |
| `"FAILED"` | Unrecoverable error (also returned by the platform on exceptions) | Marks session FAILED |

**Use `REVIEW_REQUIRED` when:**
- Your KIO found potential problems (bugs, vulnerabilities, breaking changes)
- The generated output needs human sign-off before it's applied (patches, deploys)
- Confidence in the LLM output is low (e.g. hallucination score above threshold)

**Do not return `REVIEW_REQUIRED` unnecessarily** — every HITL adds latency for the user.

---

## 10. Using the LLM

Use `shared.llm.factory.create_llm_provider()` to get the configured provider (Ollama/OpenAI/Claude — set by `LLM_PROVIDER` env var). This is the same provider the LM Engine uses.

```python
import asyncio
from shared.llm.factory import create_llm_provider

# Singleton pattern — always use a module-level lock (see kio3/main.py for reference)
_provider = None
_provider_lock = asyncio.Lock()

async def _get_provider():
    global _provider
    if _provider is None:
        async with _provider_lock:
            if _provider is None:
                _provider = await create_llm_provider()
    return _provider
```

### Making a completion call

```python
provider = await _get_provider()
response = await provider.complete(
    prompt="Analyse this function for bugs:\n\n" + code_snippet,
    system="You are an expert Python security auditor. Reply in JSON.",
)
raw_text = response.content   # str — the model's reply
```

### Parsing LLM JSON safely (IMPORTANT for qwen3b)

**Never use `json.loads()` directly on LLM output.** Use the platform's coerce helpers:

```python
from shared.llm.llm_json_coerce import extract_json_object, repair_json_text

# Best-effort JSON extraction — handles markdown fences, Python literals,
# truncated output, extra text before/after the JSON object.
parsed = extract_json_object(response.content)
if parsed is None:
    logger.warning("[{}] LLM returned non-JSON: {!r}", KIO_ID, response.content[:200])
    parsed = {}   # degrade gracefully

# Then access fields with .get() and defaults:
severity = parsed.get("severity", "medium")
findings = parsed.get("findings", [])
```

### Designing prompts for small models (qwen2.5-coder:3b)

qwen3b is fast but prone to:
- Returning Python dicts instead of JSON (`None`, `True`, `False`) — `extract_json_object` handles this
- Wrapping JSON in markdown ` ```json ``` ` — handled
- Truncating long outputs — handled via `_close_unclosed_json`
- Making up fields or missing fields — always use `.get(key, default)`

**Prompt tips:**
```python
system = (
    "Respond with ONLY a JSON object — no other text, no markdown fences.\n"
    '{"field": "value"}\n'
    "Valid values for severity: low, medium, high."
)
```

- Keep prompts under 1500 tokens for 3b models
- Ask for one structured thing per call
- If you need a list of N items, ask for them one at a time or in batches of 4 (`bug_detection_chunk_size` in config)
- Always implement a fallback for when parsing fails

---

## 11. Calling Another KIO (A2A)

If your KIO needs to invoke a peer KIO directly (without routing through the orchestrator loop), use `A2AClient`:

```python
from shared.a2a.client import get_a2a_client

async def handler(envelope: MessageEnvelope) -> dict:
    a2a = get_a2a_client()

    result = await a2a.invoke(
        "kio5",                         # target KIO ID
        session_id=envelope.session_id,
        workflow_id=envelope.workflow_id,
        caller_kio=KIO_ID,
        payload={
            "description": envelope.payload.get("description", ""),
            "findings":    my_findings,
        },
    )
    peer_artifact = result.get("payload", {}).get("artifact_data", {})
    return { "status": "DONE", "artifact_id": str(uuid.uuid4()), "artifact_data": peer_artifact, "message": "Done." }
```

**Notes:**
- A2A uses NATS JetStream if available, HTTP fallback otherwise — same as the orchestrator.
- A2A calls share the same `session_id` and `workflow_id` so their artifacts appear in the same session.
- A2A is for **sub-tasks within your step**, not for replacing the orchestrator's sequencing. If the steps should be visible in the dashboard independently, put them in `kio_sequence` instead.

---

## 12. Using MCP Tools (Filesystem, Shell)

The platform exposes filesystem and shell tools via the MCP registry. You can call them directly in Python without going through the HTTP endpoint:

```python
from shared.mcp.tools.filesystem import read_file, list_directory, write_file
from shared.mcp.tools.shell import run_command

# Read a file (max_chars defaults to 8000)
result = await read_file({"path": "/repos/target/app.py", "max_chars": 4000})
content = result["content"]

# List files in a directory
result = await list_directory({"path": "/repos/target/src", "pattern": "*.py", "recursive": True})
files = result["entries"]   # list of {"name", "path", "type", "size"}

# Write a file (creates parent dirs automatically)
await write_file({"path": "/repos/target/tests/test_auth.py", "content": test_code})

# Run a command (timeout capped at 120s, stdout truncated at 10000 chars)
result = await run_command({"command": "pytest tests/ -q", "cwd": "/repos/target", "timeout": 60})
returncode = result["returncode"]
stdout     = result["stdout"]
stderr     = result["stderr"]
timed_out  = result["timed_out"]
```

**Safety constraints:**
- `run_command` executes in the server process — only use it on trusted input
- `write_file` creates any parent directory that doesn't exist
- `read_file` is limited to `max_chars` to avoid overwhelming the LLM context
- `list_directory` returns at most 500 entries

---

## 13. Triggering HITL from Your KIO

Simply return `"status": "REVIEW_REQUIRED"` with a `"hitl_question"` string. The platform does the rest:

```python
return {
    "status": "REVIEW_REQUIRED",
    "artifact_id": str(uuid.uuid4()),
    "artifact_data": artifact_data,
    "message": f"Found {len(bugs)} confirmed bugs.",
    "hitl_question": (
        f"Found {len(bugs)} bugs. Approve auto-patch generation?\n"
        f"Highest severity: {max_sev}."
    ),
}
```

**What happens next (automatically):**
1. Orchestrator saves LangGraph state to PostgreSQL checkpoint
2. Session Manager creates a `HumanApprovalRecord`
3. SSE stream sends `HITL_CHECKPOINT` event to the dashboard
4. Dashboard shows the question + artifact viewer
5. Human clicks Approve or Reject with optional text feedback
6. Orchestrator receives `POST /workflow/{session_id}/approve`
7. LangGraph resumes with the human's feedback text in `envelope.payload["feedback"]`

**Accessing feedback after resume:**

When your KIO runs again after a HITL approval, the feedback is in the payload:

```python
feedback = payload.get("feedback", "")
if feedback:
    logger.info("[{}] Resuming with human feedback: {!r}", KIO_ID, feedback)
    # adjust your logic based on feedback
```

---

## 14. Configuration & Environment Variables

All settings live in `shared/config.py` and are loaded from `.env`.

### Variables your KIO can read

```python
from shared.config import get_settings
cfg = get_settings()

cfg.target_repo_path          # str  — default repo to analyse if no working_directory given
cfg.repo_analysis_max_files   # int  — max files to scan per KIO3-style loop (default 25)
cfg.bug_detection_chunk_size  # int  — LLM batch size (default 4)
cfg.llm_provider              # str  — "ollama" | "openai" | "claude"
cfg.ollama_model              # str  — e.g. "qwen2.5-coder:3b"
cfg.kio_port_map              # dict — {"kio4": 8014, ...}
cfg.use_nats                  # bool — True = NATS transport; False = HTTP only
```

### Variables you must NOT hard-code

Never hard-code port numbers or service URLs in your handler. Always use:
```python
cfg.session_manager_url   # "http://localhost:8002" locally
cfg.lm_engine_url         # "http://localhost:8001"
cfg.kio_port_map[KIO_ID]  # your own port
```

### Adding KIO-specific settings

If your KIO needs a custom setting (e.g. `KIO8_THRESHOLD=0.7`), add it to `Settings` in `shared/config.py`:

```python
# in shared/config.py > class Settings
kio8_confidence_threshold: float = 0.7
```

Then set it in `.env`:
```
KIO8_CONFIDENCE_THRESHOLD=0.85
```

And in `docker-compose.yml` under your service:
```yaml
kio8:
  environment:
    <<: *common-env
    KIO_ID: kio8
    KIO_PORT: 8018
    KIO8_CONFIDENCE_THRESHOLD: "0.85"
```

---

## 15. Running Locally (Without Docker)

### 1. Copy and configure .env

```bash
cp .env.example .env
# Edit .env — at minimum set:
# JWT_SECRET_KEY=<any long random string>
# LLM_PROVIDER=ollama   (or mock for instant testing without Ollama)
```

### 2. Start infrastructure

```bash
# Option A: full local stack (requires Docker for postgres + nats)
docker compose up postgres nats -d

# Option B: no NATS (HTTP-only mode)
# set USE_NATS=false in .env
docker compose up postgres -d
```

### 3. Run migrations

```bash
cd shared && alembic upgrade head && cd ..
```

### 4. Start services

Open separate terminals (or use a process manager like `honcho`):

```bash
# Terminal 1 — Session Manager
PYTHONPATH=. uvicorn apps.session_manager.main:app --port 8002

# Terminal 2 — LM Engine
PYTHONPATH=. uvicorn apps.lm_engine.main:app --port 8001

# Terminal 3 — Your KIO (replace kio4 with yours)
PYTHONPATH=. uvicorn apps.kio_shells.kio4.main:app --port 8014 --reload

# Terminal 4 — Orchestrator
PYTHONPATH=. uvicorn apps.orchestrator.main:app --port 8000
```

Or use the provided script:
```bash
bash run_all.sh
```

### 5. Test your KIO directly (without orchestrator)

```bash
# Health check
curl http://localhost:8014/health/

# Direct execute (bypass NATS — always works)
curl -X POST http://localhost:8014/execute \
  -H "Content-Type: application/json" \
  -d '{
    "message_id": "test-001",
    "correlation_id": "corr-001",
    "step_id": "step-001",
    "protocol_version": "1.0.0",
    "project_id": "kio1-platform",
    "session_id": "test-session",
    "workflow_id": "test-workflow",
    "source": "test",
    "target": "kio4",
    "timestamp": "2026-01-01T00:00:00Z",
    "message_type": "JOB_REQUEST",
    "payload": {
      "description": "Generate tests for the auth module",
      "working_directory": "./examples/buggy_fastapi_repo"
    }
  }'
```

---

## 16. Running With Docker Compose

```bash
# Build and start everything
docker compose up --build

# Rebuild only your KIO after code changes
docker compose build kio4 && docker compose up kio4 -d

# View logs
docker compose logs -f kio4

# Run the full stack in background
docker compose up -d

# Stop everything
docker compose down
```

### Your KIO's Docker entry point

The platform uses a single `Dockerfile` for all KIO shells. It selects your KIO via `ARG KIO_ID` and `ENV KIO_PORT`. You do **not** need to write a Dockerfile — just ensure your `main.py` ends with:

```python
if __name__ == "__main__":
    import uvicorn
    cfg = get_settings()
    uvicorn.run("main:app", host=cfg.api_host, port=cfg.kio_port_map.get(KIO_ID, 8000), reload=False)
```

---

## 17. Testing Your KIO

### Unit test the handler directly

```python
# tests/test_kio4_handler.py
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

# Import your handler directly — no HTTP server needed
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / "apps/kio_shells"))
sys.path.insert(0, str(Path(__file__).parents[1]))

from kio_base import MessageEnvelope
from apps.kio_shells.kio4.main import handler


@pytest.mark.asyncio
async def test_handler_returns_done():
    envelope = MessageEnvelope(
        message_id="test",
        correlation_id="corr-1",
        step_id="step-1",
        protocol_version="1.0.0",
        project_id="kio1-platform",
        session_id="sess-1",
        workflow_id="wf-1",
        source="test",
        target="kio4",
        timestamp="2026-01-01T00:00:00Z",
        message_type="JOB_REQUEST",
        payload={"description": "Generate tests", "working_directory": "."},
    )

    # Mock the LLM so tests are fast and deterministic
    with patch("apps.kio_shells.kio4.main._get_provider") as mock_prov:
        mock_prov.return_value = AsyncMock()
        mock_prov.return_value.complete.return_value.content = '{"tests": ["test_a", "test_b"]}'

        result = await handler(envelope)

    assert result["status"] in ("DONE", "REVIEW_REQUIRED")
    assert "artifact_id" in result
    assert isinstance(result["artifact_data"], dict)
    assert "message" in result


@pytest.mark.asyncio
async def test_handler_degrades_on_llm_failure():
    """Even when LLM is completely down, handler must return a valid dict, not raise."""
    envelope = MessageEnvelope(
        message_id="test",
        session_id="sess-1",
        workflow_id="wf-1",
        source="test",
        target="kio4",
        timestamp="2026-01-01T00:00:00Z",
        message_type="JOB_REQUEST",
        payload={"description": "any", "working_directory": "."},
    )

    with patch("apps.kio_shells.kio4.main._get_provider", side_effect=Exception("Ollama down")):
        result = await handler(envelope)

    # Must never raise — must return a usable dict
    assert "status" in result
    assert "artifact_id" in result
```

Run tests:
```bash
PYTHONPATH=. pytest tests/test_kio4_handler.py -v
```

### Integration test through the orchestrator

```bash
# 1. Get a JWT
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"secret"}' | jq -r .access_token)

# 2. Start a workflow that hits your KIO
SESSION=$(curl -s -X POST http://localhost:8000/workflow/run \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"kio_sequence\": [\"kio4\"],
    \"description\": \"Generate tests for auth module\",
    \"working_directory\": \"./examples/buggy_fastapi_repo\"
  }" | jq -r .session_id)

echo "Session: $SESSION"

# 3. Poll status
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/workflow/$SESSION/status | jq .
```

---

## 18. Handoff Checklist

Before handing off your KIO to the platform team, confirm:

### Code
- [ ] `handler()` is `async def` and takes exactly one argument: `envelope: MessageEnvelope`
- [ ] Handler always returns a `dict` — never raises an unhandled exception
- [ ] All LLM calls use `extract_json_object()` for parsing, with a fallback for `None`
- [ ] `_get_provider()` uses `asyncio.Lock` with double-checked pattern
- [ ] No `time.sleep()` calls — use `await asyncio.sleep()` for any waits
- [ ] No hardcoded port numbers, URLs, or paths — everything comes from `get_settings()`
- [ ] `KIO_ID` constant matches your KIO name (e.g. `"kio8"`)
- [ ] The `if __name__ == "__main__":` block uses `cfg.kio_port_map.get(KIO_ID, <fallback>)`

### Testing
- [ ] `curl http://localhost:{your_port}/health/` returns `{"status": "ok"}`
- [ ] `POST /execute` with a minimal payload returns a valid dict (not a 500 error)
- [ ] Handler returns a valid dict even when the LLM is down (graceful degradation)
- [ ] Tested with `LLM_PROVIDER=mock` to confirm logic works without Ollama
- [ ] Unit test exists covering at least: happy path + LLM failure degradation

### Contracts
- [ ] `artifact_data` always contains `"kio"` and `"produced_at"` keys
- [ ] When `status == "REVIEW_REQUIRED"`, `"hitl_question"` is non-empty
- [ ] All fields accessed from `envelope.payload` use `.get(key, default)` — never direct `["key"]`

### Docker
- [ ] `docker compose build kio<N>` completes without errors
- [ ] `docker compose up kio<N>` starts and the health check passes

---

## 19. Common Mistakes

### Mistake 1 — Raising exceptions instead of degrading

**Wrong:**
```python
async def handler(envelope):
    provider = await _get_provider()   # might raise OllamaUnavailableError
    result = json.loads(await provider.complete(...).content)  # might raise JSONDecodeError
    return {"status": "DONE", ...}
```

**Right:**
```python
async def handler(envelope):
    try:
        provider = await _get_provider()
        raw = (await provider.complete(...)).content
        parsed = extract_json_object(raw) or {}
        return {"status": "DONE", "artifact_id": str(uuid.uuid4()),
                "artifact_data": parsed, "message": "Done."}
    except Exception as exc:
        logger.exception("[{}] handler failed: {}", KIO_ID, exc)
        return {"status": "REVIEW_REQUIRED", "artifact_id": str(uuid.uuid4()),
                "artifact_data": {"kio": KIO_ID, "error": str(exc)},
                "message": f"{KIO_ID} failed: {exc}",
                "hitl_question": f"{KIO_ID} encountered an error. Continue?"}
```

### Mistake 2 — Using `json.loads()` directly on LLM output

**Wrong:** `json.loads(response.content)` — crashes on markdown fences, Python literals, truncated JSON.

**Right:** `extract_json_object(response.content)` — returns `dict | None`, never raises.

### Mistake 3 — Blocking the event loop

**Wrong:** `time.sleep(2)`, `subprocess.run(...)`, `open(path).read()`

**Right:**
```python
await asyncio.sleep(2)
result = await run_command({"command": "...", "timeout": 30})
content = (await read_file({"path": path}))["content"]
```

### Mistake 4 — Hardcoding ports

**Wrong:** `uvicorn.run("main:app", port=8014)`

**Right:** `uvicorn.run("main:app", port=cfg.kio_port_map.get(KIO_ID, 8014))`

### Mistake 5 — No provider lock

**Wrong:**
```python
_provider = None
async def _get_provider():
    global _provider
    if _provider is None:
        _provider = await create_llm_provider()   # race: called twice concurrently
    return _provider
```

**Right:** Always use `asyncio.Lock()` with double-checked pattern (copy from kio3/main.py).

### Mistake 6 — Returning `REVIEW_REQUIRED` without `hitl_question`

The orchestrator will use a generic question if you omit it, but provide a specific one — it's what the human sees in the dashboard.

### Mistake 7 — Direct dict access on payload

**Wrong:** `envelope.payload["description"]` — raises `KeyError` if orchestrator changes payload shape.

**Right:** `envelope.payload.get("description", "")` — always has a default.

---

## Quick Reference

```python
# Minimal correct handler template
import asyncio, uuid
from datetime import datetime, timezone
from loguru import logger
from shared.config import get_settings
from shared.llm.factory import create_llm_provider
from shared.llm.llm_json_coerce import extract_json_object
from kio_base import MessageEnvelope

KIO_ID = "kioN"
_provider = None
_provider_lock = asyncio.Lock()

async def _get_provider():
    global _provider
    if _provider is None:
        async with _provider_lock:
            if _provider is None:
                _provider = await create_llm_provider()
    return _provider

async def handler(envelope: MessageEnvelope) -> dict:
    payload = envelope.payload
    try:
        # --- your work ---
        provider = await _get_provider()
        resp = await provider.complete("Do X", system="Return JSON only.")
        parsed = extract_json_object(resp.content) or {}
        result_data = parsed.get("results", [])
        # -----------------
        return {
            "status": "DONE",
            "artifact_id": str(uuid.uuid4()),
            "artifact_data": {
                "kio": KIO_ID,
                "results": result_data,
                "produced_at": datetime.now(timezone.utc).isoformat(),
            },
            "message": f"Completed with {len(result_data)} items.",
        }
    except Exception as exc:
        logger.exception("[{}] failed: {}", KIO_ID, exc)
        return {
            "status": "REVIEW_REQUIRED",
            "artifact_id": str(uuid.uuid4()),
            "artifact_data": {"kio": KIO_ID, "error": str(exc),
                              "produced_at": datetime.now(timezone.utc).isoformat()},
            "message": f"{KIO_ID} failed: {exc}",
            "hitl_question": f"{KIO_ID} encountered an error. Continue?",
        }
```

---

*For questions, contact the platform team or open an issue in the repository.*
