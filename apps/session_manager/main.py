"""Session Manager — PostgreSQL-backed session lifecycle service.

Run from the EnisAliMerge root with PYTHONPATH set:
    PYTHONPATH=. uvicorn apps.session_manager.main:app --port 8002
Or via run_all.sh which sets PYTHONPATH automatically.
"""
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Allow `from src.api...` imports when run directly from this directory
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI
from loguru import logger

from shared.config import get_settings
from shared.persistence.database import dispose_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_settings()
    logger.info("Session Manager starting on port {}", cfg.session_manager_port)
    yield
    await dispose_engine()
    logger.info("Session Manager shut down")


app = FastAPI(
    title="Session Manager",
    description="PostgreSQL-backed session lifecycle, HITL, and artifact lineage service.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health/")
async def health():
    return {"status": "ok", "service": "session_manager"}


from src.api.router import router as sessions_router  # noqa: E402
app.include_router(sessions_router)


if __name__ == "__main__":
    import uvicorn
    cfg = get_settings()
    uvicorn.run("main:app", host=cfg.api_host, port=cfg.session_manager_port, reload=False)
