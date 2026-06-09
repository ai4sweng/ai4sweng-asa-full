"""PostgreSQL LangGraph checkpointer factory.

Replaces the in-memory MemorySaver so workflow graph state survives process
restarts.  Uses ``langgraph-checkpoint-postgres`` with an async connection
pool (psycopg3 + psycopg-pool).

LangGraph tables created by ``checkpointer.setup()`` (idempotent):
  • checkpoints
  • checkpoint_blobs
  • checkpoint_writes

These are separate from the application's Alembic-managed tables and live in
the same PostgreSQL database under the ``langgraph_`` prefix.

Lifecycle
---------
  1. Orchestrator lifespan calls ``init_checkpointer()`` on startup.
  2. All WorkflowRunner graph calls use the singleton returned by
     ``get_checkpointer()``.
  3. Orchestrator lifespan calls ``close_checkpointer()`` on shutdown.

Fallback
--------
  If PostgreSQL is unreachable at startup, a MemorySaver is used instead and
  a warning is emitted.  This allows local dev without a running database.
"""
from __future__ import annotations

import asyncio

from loguru import logger

from shared.config import get_settings

# --- module-level singletons ---
_checkpointer = None
_pool = None
_lock = asyncio.Lock()


async def init_checkpointer():
    """Connect to PostgreSQL and prepare LangGraph checkpoint tables.

    Idempotent — safe to call multiple times.
    """
    global _checkpointer, _pool

    async with _lock:
        if _checkpointer is not None:
            return _checkpointer

        cfg = get_settings()
        # asyncpg DSN  → psycopg3 DSN  (strip '+asyncpg' dialect)
        dsn = cfg.database_url.replace("postgresql+asyncpg://", "postgresql://")

        try:
            from psycopg_pool import AsyncConnectionPool
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            _pool = AsyncConnectionPool(
                conninfo=dsn,
                max_size=cfg.checkpointer_pool_size,
                open=False,
                kwargs={"autocommit": True, "prepare_threshold": 0},
            )
            await _pool.open()

            _checkpointer = AsyncPostgresSaver(_pool)
            await _checkpointer.setup()
            logger.info("PostgreSQL LangGraph checkpointer ready (crash-safe graph state)")

        except Exception as exc:
            logger.warning(
                "PostgreSQL checkpointer unavailable ({}); falling back to MemorySaver. "
                "Graph state will NOT survive restarts.",
                exc,
            )
            from langgraph.checkpoint.memory import MemorySaver
            _checkpointer = MemorySaver()

    return _checkpointer


async def get_checkpointer():
    """Return the active checkpointer, initialising it on first call."""
    if _checkpointer is None:
        return await init_checkpointer()
    return _checkpointer


async def close_checkpointer() -> None:
    """Drain the connection pool on orchestrator shutdown."""
    global _checkpointer, _pool
    if _pool is not None:
        try:
            await _pool.close()
            logger.info("Checkpointer connection pool closed")
        except Exception as exc:
            logger.warning("Error closing checkpointer pool: {}", exc)
        _pool = None
    _checkpointer = None
