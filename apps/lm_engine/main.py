"""LM Engine — unified HTTP inference service wrapping shared.llm.

Run from the EnisAliMerge root with PYTHONPATH set:
    PYTHONPATH=. uvicorn apps.lm_engine.main:app --port 8001
Or via run_all.sh which sets PYTHONPATH automatically.
"""
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI
from loguru import logger

from shared.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_settings()
    logger.info("LM Engine starting — provider={} port={}", cfg.llm_provider, cfg.lm_engine_port)
    yield
    logger.info("LM Engine shut down")


app = FastAPI(
    title="LM Engine",
    description="Unified LLM inference service. Default: Ollama/qwen2.5-coder:3b.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health/")
async def health():
    cfg = get_settings()
    return {"status": "ok", "service": "lm_engine", "provider": cfg.llm_provider}


from src.api.router import router as llm_router  # noqa: E402
app.include_router(llm_router)


if __name__ == "__main__":
    import uvicorn
    cfg = get_settings()
    uvicorn.run("main:app", host=cfg.api_host, port=cfg.lm_engine_port, reload=False)
