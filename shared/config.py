"""
Platform-wide settings loaded from environment / .env file.

Architectural constants belong in contracts/. Never mix the two.
"""

import os
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # PostgreSQL — System of Record
    database_url: str = "postgresql+asyncpg://enisalimerge:enisalimerge@localhost:5432/enisalimerge"

    # NATS — inter-service event bus
    nats_url: str = "nats://localhost:4222"

    # Redis — UI event streaming
    redis_url: str = "redis://localhost:6379"

    # Langfuse — observability (optional; degrades gracefully)
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    otel_exporter_otlp_endpoint: str = "https://cloud.langfuse.com/api/public/otel/v1/traces"

    # Platform identity stamped on all envelopes
    project_id: str = "enisalimerge-kio1"

    # Auth
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # LM Engine
    lm_engine_url: str = "http://localhost:8001"
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

    # Service URLs
    session_manager_url: str = "http://localhost:8002"
    report_tool_url: str = "http://localhost:8003"

    # Orchestrator API
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Auto-approve HITL (CI/test only)
    auto_approve_human: bool = False

    # Database connection pool
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # Service ports (override per-service via env)
    orchestrator_port: int = 8000
    lm_engine_port: int = 8001
    session_manager_port: int = 8002

    # KIO shell ports — override via KIO_PORT_MAP env var as JSON string
    kio_port_map: dict[str, int] = {
        "kio2": 8012, "kio3": 8013, "kio4": 8014, "kio5": 8015,
        "kio6": 8016, "kio7": 8017, "kio8": 8018, "kio9": 8019,
        "kio10": 8020, "kio11": 8021, "kio12": 8022, "kio13": 8023,
    }

    # KIO base host for HTTP transport.
    # Set to the container service name prefix (e.g. "" → "kio3") in Docker,
    # or leave as "localhost" for local dev.  When set to "" (empty string),
    # KioClient and A2AClient use the kio_id itself as the hostname (Docker DNS).
    kio_base_host: str = "localhost"

    # HTTP client timeouts (seconds)
    session_manager_client_timeout: int = 30
    lm_engine_client_timeout: int = 60
    kio_client_timeout: int = 300

    # Approval polling timeout (seconds) — workflow hangs if exceeded
    hitl_approval_timeout: int = 300

    # Messaging transport — set use_nats=false to fall back to HTTP for local dev without NATS
    use_nats: bool = True
    nats_kio_stream: str = "KIO_JOBS"
    nats_request_timeout: int = 120  # seconds to wait for KIO reply

    # Logging
    log_level: str = "INFO"

    # Target repo for examples/buggy_fastapi_repo
    target_repo_path: str = "examples/buggy_fastapi_repo"

    @field_validator("jwt_secret_key", mode="after")
    @classmethod
    def _reject_default_jwt_secret(cls, value: str) -> str:
        if value == "change-me-in-production" and os.environ.get("ENV", "dev") == "production":
            raise ValueError(
                "JWT_SECRET_KEY must be overridden in production — "
                "set the JWT_SECRET_KEY environment variable."
            )
        return value

    @field_validator("target_repo_path", mode="before")
    @classmethod
    def _normalize_target_repo_path(cls, value: object) -> str:
        if value is None:
            return "examples/buggy_fastapi_repo"
        text = str(value).strip()
        return text or "examples/buggy_fastapi_repo"

    # Repo analysis limits (tuned for local models on 16 GB RAM)
    repo_analysis_max_excerpt_chars: int = 3500
    repo_analysis_max_files: int = 25
    bug_detection_chunk_size: int = 4


@lru_cache
def get_settings() -> Settings:
    return Settings()
