"""
Structured error types for KIO failures, compensation, and replay.
"""

from pydantic import BaseModel


class KIOError(BaseModel):
    """Structured error returned by a KIO on failure."""

    error_code: str
    message: str
    kio_id: str
    task_id: str | None = None
    workflow_id: str | None = None
    retryable: bool = False
    details: dict = {}


class CompensationError(KIOError):
    """Error during compensation — requires manual intervention."""

    compensated_task_id: str
    compensation_action: str


# Standard error codes (extend shared/constants.ErrorCode as needed)
class KIOErrorCode:
    KIO_UNAVAILABLE = "KIO_UNAVAILABLE"
    KIO_TIMEOUT = "KIO_TIMEOUT"
    KIO_FAILED = "KIO_FAILED"
    INVALID_INPUT_ARTIFACT = "INVALID_INPUT_ARTIFACT"
    INVALID_OUTPUT_ARTIFACT = "INVALID_OUTPUT_ARTIFACT"
    LLM_PARSE_FAILURE = "LLM_PARSE_FAILURE"
    NATS_PUBLISH_FAILED = "NATS_PUBLISH_FAILED"
    COMPENSATION_FAILED = "COMPENSATION_FAILED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    FORBIDDEN = "FORBIDDEN"
