"""
Platform-wide settings loaded from environment / .env file.

Single source of truth for ALL ports, hosts, timeouts, and tuning constants.
No magic numbers appear elsewhere in the codebase — always import from here.

URL construction
----------------
Service-to-service URLs (lm_engine_url, session_manager_url, nats_url,
database_url, etc.) are auto-computed from their individual host + port
parts when not set explicitly.  You can still override a full URL directly:

    DATABASE_URL=postgresql+asyncpg://user:pass@prod-host:5432/mydb

and it will take precedence over the individual parts.  Set the parts to
target a different host/port per environment without touching URL syntax.
"""

from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── PostgreSQL — System of Record ─────────────────────────────────────────
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "enisalimerge"
    postgres_password: str = "enisalimerge"
    postgres_db: str = "enisalimerge"
    # Auto-computed from the parts above; override with DATABASE_URL for remote DBs
    database_url: str = ""

    # ── NATS — inter-service event bus ────────────────────────────────────────
    nats_host: str = "localhost"
    nats_port: int = 4222
    # Auto-computed from nats_host:nats_port; override with NATS_URL for remote
    nats_url: str = ""

    # ── Langfuse — observability (optional; degrades gracefully) ──────────────
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    otel_exporter_otlp_endpoint: str = "https://cloud.langfuse.com/api/public/otel/v1/traces"

    # ── Platform identity stamped on all envelopes ────────────────────────────
    project_id: str = "enisalimerge-kio1"

    # ── Auth ──────────────────────────────────────────────────────────────────
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # ── Service ports ─────────────────────────────────────────────────────────
    orchestrator_port: int = 8000
    lm_engine_port: int = 8001
    session_manager_port: int = 8002
    report_tool_port: int = 8003
    dashboard_port: int = 3000
    vite_dev_port: int = 5173

    # ── Service hosts ─────────────────────────────────────────────────────────
    # In Docker set these to container service names (e.g. "lm_engine").
    # Locally "localhost" covers all.
    api_host: str = "0.0.0.0"             # bind address for uvicorn in every service
    lm_engine_host: str = "localhost"
    session_manager_host: str = "localhost"
    report_tool_host: str = "localhost"

    # ── Service URLs (auto-computed; override for remote / Docker targets) ────
    lm_engine_url: str = ""        # → http://{lm_engine_host}:{lm_engine_port}
    session_manager_url: str = ""  # → http://{session_manager_host}:{session_manager_port}
    report_tool_url: str = ""      # → http://{report_tool_host}:{report_tool_port}

    # ── LLM providers ─────────────────────────────────────────────────────────
    llm_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:3b"
    ollama_max_tokens: int = 2048
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    # When non-empty, HITL is triggered on pipeline failure offering this as the retry provider
    llm_provider_fallback: str = ""

    # ── Auto-approve HITL (CI/test only) ─────────────────────────────────────
    auto_approve_human: bool = False

    # ── Database connection pool ───────────────────────────────────────────────
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ── KIO shell ports ───────────────────────────────────────────────────────
    # All 13 KIO ports in one place.  Override the whole map via KIO_PORT_MAP
    # as a JSON string, or add individual port fields if needed.
    kio_port_map: dict[str, int] = {
        "kio1": 8011, "kio2": 8012, "kio3": 8013, "kio4": 8014, "kio5": 8015,
        "kio6": 8016, "kio7": 8017, "kio8": 8018, "kio9": 8019,
        "kio10": 8020, "kio11": 8021, "kio12": 8022, "kio13": 8023,
    }

    # KIO base host for HTTP transport.
    # Set to "" in Docker so KioClient uses the kio_id as Docker DNS hostname.
    kio_base_host: str = "localhost"

    # ── HTTP client timeouts (seconds) ────────────────────────────────────────
    session_manager_client_timeout: int = 30
    lm_engine_client_timeout: int = 60
    kio_client_timeout: int = 300
    hitl_approval_timeout: int = 300

    # ── NATS stream / consumer parameters ─────────────────────────────────────
    nats_kio_stream: str = "KIO_JOBS"
    nats_request_timeout: int = 120       # seconds to wait for a KIO reply
    nats_stream_max_age: int = 3600       # seconds — KIO_JOBS message TTL (dead-letter expiry)
    nats_max_deliver: int = 3             # max redelivery attempts before drop
    nats_fetch_timeout: float = 2.0       # seconds — pull-consumer poll interval (no-message wait)
    nats_reconnect_wait: int = 2          # seconds between NATS reconnect attempts
    nats_max_reconnect_attempts: int = 10 # 0 = unlimited

    # Use NATS transport?  false = HTTP-only mode (no NATS server required)
    use_nats: bool = True

    # ── CORS ──────────────────────────────────────────────────────────────────
    # Auto-computed from vite_dev_port + dashboard_port when empty.
    # Override with a JSON list for production:
    #   CORS_ORIGINS=["https://app.example.com"]
    cors_origins: list[str] = []

    # ── Orchestrator rate-limiting ─────────────────────────────────────────────
    max_active_sessions_per_user: int = 20  # max concurrent ACTIVE sessions per user

    # ── SSE event bus ─────────────────────────────────────────────────────────
    sse_queue_maxsize: int = 256    # max events buffered per subscriber before drops
    sse_heartbeat_interval: int = 15  # seconds between SSE keepalive pings

    # ── KIO lifecycle timing ──────────────────────────────────────────────────
    kio_heartbeat_interval: int = 30    # seconds between KIO HEARTBEAT publishes
    kio_capability_interval: int = 60   # seconds between CAPABILITY_ANNOUNCEMENT publishes
    agent_stale_threshold: int = 90     # seconds without announcement → endpoint stale
    timeout_monitor_interval: int = 5   # seconds between TimeoutMonitor deadline sweeps

    # ── KIO subprocess timeouts (kio7: pip install and pytest) ───────────────
    kio_subprocess_pip_timeout: int = 120  # seconds for pip install / dependency setup
    kio_subprocess_test_timeout: int = 30  # seconds for a single pytest collect/run

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = "INFO"

    # ── Target repo ───────────────────────────────────────────────────────────
    target_repo_path: str = "examples/buggy_fastapi_repo"

    # ── LangGraph checkpointer ────────────────────────────────────────────────
    checkpointer_pool_size: int = 5  # AsyncConnectionPool size for AsyncPostgresSaver

    # ── Repo analysis limits (tuned for local models on 16 GB RAM) ───────────
    repo_analysis_max_excerpt_chars: int = 3500
    repo_analysis_max_files: int = 25
    bug_detection_chunk_size: int = 4

    # ── URL auto-computation ──────────────────────────────────────────────────

    @model_validator(mode="after")
    def _build_urls(self) -> "Settings":
        """Derive service URLs from host + port parts when not set explicitly.

        A non-empty env var (DATABASE_URL, LM_ENGINE_URL, …) always wins.
        Individual parts (POSTGRES_HOST, LM_ENGINE_PORT, …) are the fallback.
        """
        if not self.database_url:
            self.database_url = (
                f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        if not self.nats_url:
            self.nats_url = f"nats://{self.nats_host}:{self.nats_port}"
        if not self.lm_engine_url:
            self.lm_engine_url = f"http://{self.lm_engine_host}:{self.lm_engine_port}"
        if not self.session_manager_url:
            self.session_manager_url = (
                f"http://{self.session_manager_host}:{self.session_manager_port}"
            )
        if not self.report_tool_url:
            self.report_tool_url = (
                f"http://{self.report_tool_host}:{self.report_tool_port}"
            )
        if not self.cors_origins:
            self.cors_origins = [
                f"http://localhost:{self.vite_dev_port}",
                f"http://localhost:{self.dashboard_port}",
            ]
        return self

    # ── Field validators ──────────────────────────────────────────────────────

    @field_validator("jwt_secret_key", mode="after")
    @classmethod
    def _reject_default_jwt_secret(cls, value: str) -> str:
        if value == "change-me-in-production":
            import sys
            print(
                "\nFATAL: JWT_SECRET_KEY is set to the insecure default value.\n"
                "Generate a secret with:  openssl rand -hex 32\n"
                "Then set JWT_SECRET_KEY=<result> in your .env file.\n",
                file=sys.stderr,
            )
            raise ValueError("JWT_SECRET_KEY must be overridden — refusing to start")
        if len(value) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters")
        return value

    @field_validator("target_repo_path", mode="before")
    @classmethod
    def _normalize_target_repo_path(cls, value: object) -> str:
        if value is None:
            return "examples/buggy_fastapi_repo"
        text = str(value).strip()
        return text or "examples/buggy_fastapi_repo"


@lru_cache
def get_settings() -> Settings:
    return Settings()
