"""KIO1 Orchestrator — central workflow execution engine.

Run from the EnisAliMerge root with PYTHONPATH set:
    PYTHONPATH=. uvicorn apps.orchestrator.main:app --port 8000
Or via run_all.sh which sets PYTHONPATH automatically.
"""
import asyncio
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
    try:
        async with asyncio.timeout(30):
            await init_checkpointer()
    except TimeoutError:
        logger.warning("Checkpointer init timed out — will use MemorySaver fallback")

    # Initialise WorkflowRunner so get_runner() is always safe in request handlers.
    from src.engine.workflow_runner import init_runner
    try:
        async with asyncio.timeout(15):
            await init_runner()
    except TimeoutError:
        logger.error("WorkflowRunner init timed out — service may not be functional")

    # Initialise orchestrator state machine
    from src.engine.orchestrator_state import get_orchestrator_sm
    orch_sm = get_orchestrator_sm()
    logger.info("Orchestrator state machine ready — state={}", orch_sm.state.value)

    if cfg.use_nats:
        try:
            import json as _json
            from shared.messaging.jetstream import get_jetstream
            from src.engine.agent_registry import get_agent_registry
            from src.engine.event_bus import get_event_bus, WorkflowEvent

            js = await get_jetstream()
            logger.info("JetStream connected — KIO requests will use NATS transport")

            registry = get_agent_registry()
            bus = get_event_bus()

            async def _on_capability(msg):
                try:
                    data = _json.loads(msg.data.decode())
                    await registry.handle_announcement(data)
                except Exception as exc:
                    logger.warning("Capability announcement parse error: {}", exc)

            async def _on_task_status(msg):
                """Receive intermediate TASK_STATUS from KIOs and re-emit as SSE."""
                try:
                    data = _json.loads(msg.data.decode())
                    session_id = data.get("task_id", "")
                    kio_id = data.get("agent_id", "")
                    progress = data.get("progress", 0)
                    message = data.get("message", "")
                    bus.publish(WorkflowEvent(
                        "TASK_PROGRESS",
                        session_id,
                        f"[{kio_id.upper()}] {message} ({progress}%)",
                        {"kio": kio_id, "progress_pct": progress, "message": message},
                    ))
                except Exception as exc:
                    logger.debug("Task status parse error: {}", exc)

            await js.subscribe_core("kio.*.capability", _on_capability)
            logger.info("Subscribed to kio.*.capability — dynamic agent discovery active")
            await js.subscribe_core("kio.*.status", _on_task_status)
            logger.info("Subscribed to kio.*.status — intermediate task progress active")
        except Exception as exc:
            logger.warning("NATS unavailable at startup ({}); KIO transport = HTTP fallback", exc)

    # Start timeout monitor
    from src.engine.workflow_runner import get_runner as _get_runner
    from src.engine.timeout_monitor import get_timeout_monitor
    try:
        runner = _get_runner()
        tm = get_timeout_monitor(runner._active)
        tm.set_cancel_callback(runner.cancel)
        tm.start()
    except Exception as exc:
        logger.warning("TimeoutMonitor could not start: {}", exc)

    yield

    # Stop timeout monitor
    try:
        from src.engine.timeout_monitor import get_timeout_monitor as _gtm
        _gtm().stop()
    except Exception:
        pass

    # Notify orchestrator SM of shutdown
    try:
        from src.engine.orchestrator_state import get_orchestrator_sm as _gsm
        _gsm().shutdown()
    except Exception:
        pass

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

_cfg = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cfg.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health/")
async def health():
    return {"status": "ok", "service": "orchestrator"}


@app.get("/agents")
async def list_agents():
    """List all KIO agents that have announced themselves via CAPABILITY_ANNOUNCEMENT."""
    from src.engine.agent_registry import get_agent_registry
    return {"agents": get_agent_registry().list_agents()}


@app.get("/status")
async def orchestrator_status():
    """Orchestrator state machine status — Slide 16 of the interface spec."""
    from src.engine.orchestrator_state import get_orchestrator_sm
    return get_orchestrator_sm().summary()


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
