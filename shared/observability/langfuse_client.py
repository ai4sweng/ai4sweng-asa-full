"""
Langfuse client wrapper.

Langfuse is required from V1 but optional at runtime — missing credentials
cause graceful degradation to local logging only.
"""

import logging
from typing import Any

from shared.config import get_settings

logger = logging.getLogger(__name__)

_langfuse_client: Any = None
_enabled: bool | None = None


def is_langfuse_enabled() -> bool:
    """Return True if Langfuse credentials are configured."""
    global _enabled
    if _enabled is not None:
        return _enabled
    settings = get_settings()
    _enabled = bool(settings.langfuse_public_key and settings.langfuse_secret_key)
    if not _enabled:
        logger.info("Langfuse credentials missing — observability will log locally only")
    return _enabled


def get_langfuse_client() -> Any | None:
    """
    Return Langfuse client or None when credentials are absent.

    Lazy initialization avoids import errors when Langfuse is not configured.
    """
    global _langfuse_client
    if not is_langfuse_enabled():
        return None
    if _langfuse_client is None:
        try:
            from langfuse import Langfuse

            settings = get_settings()
            _langfuse_client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
                # Export all agent/workflow spans (not only LLM generations).
                should_export_span=lambda _span: True,
            )
            logger.info("Langfuse client initialized")
        except Exception:
            logger.exception("Failed to initialize Langfuse — continuing without it")
            return None
    return _langfuse_client
