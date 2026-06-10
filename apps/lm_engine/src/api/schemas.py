"""LM Engine request/response schemas."""

from __future__ import annotations
from pydantic import BaseModel


class CompleteRequest(BaseModel):
    prompt: str
    system: str | None = None
    caller: str = "unknown"
    task_type: str = "generation"


class CompleteResponse(BaseModel):
    content: str
    tokens_in: int
    tokens_out: int
    model: str
    latency_ms: float
