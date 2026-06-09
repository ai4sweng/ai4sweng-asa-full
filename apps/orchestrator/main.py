"""KIO1 Orchestrator — central workflow execution engine.

Run from the EnisAliMerge root with PYTHONPATH set:
    PYTHONPATH=. uvicorn apps.orchestrator.main:app --port 8000
Or via run_all.sh which sets PYTHONPATH automatically.
"""
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from shared.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_settings()
    logger.info(
        "Orchestrator starting — session_manager={} lm_engine={} port={} nats={}",
        cfg.session_manager_url, cfg.lm_engine_url, cfg.orchestrator_port, cfg.use_nats,
    )

    # Initialise PostgreSQL LangGraph checkpointer early so the first workflow
    # request doesn't block on pool setup.
    from src.engine.checkpointer import init_checkpointer
    await init_checkpointer()

    # Initialise WorkflowRunner so get_runner() is always safe in request handlers.
    from src.engine.workflow_runner import init_runner
    await init_runner()

    if cfg.use_nats:
        try:
            from shared.messaging.jetstream import get_jetstream
            await get_jetstream()
            logger.info("JetStream connected — KIO requests will use NATS transport")
        except Exception as exc:
            logger.warning("NATS unavailable at startup ({}); KIO transport = HTTP fallback", exc)

    yield

    # Graceful shutdown
    if cfg.use_nats:
        try:
            from shared.messaging.jetstream import _manager
            if _manager:
                await _manager.close()
        except Exception:
            pass

    from src.engine.checkpointer import close_checkpointer
    await close_checkpointer()

    logger.info("Orchestrator shut down")


app = FastAPI(
    title="KIO1 Orchestrator",
    description=(
        "Central workflow orchestration engine powered by LangGraph. "
        "Coordinates KIO shells, Session Manager, and LM Engine. "
        "Authenticate via POST /auth/login — all /workflow/* endpoints require Bearer JWT."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

# Allow the React dashboard (localhost:5173) to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health/")
async def health():
    return {"status": "ok", "service": "orchestrator"}


from src.api.router import router as workflow_router          # noqa: E402
from src.api.auth_router import router as auth_router        # noqa: E402
from src.api.mcp_router import router as mcp_router          # noqa: E402

app.include_router(auth_router)
app.include_router(workflow_router)
app.include_router(mcp_router)


if __name__ == "__main__":
    import uvicorn
    cfg = get_settings()
    uvicorn.run("main:app", host=cfg.api_host, port=cfg.orchestrator_port, reload=False)
