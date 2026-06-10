"""
KIO Capability schema — the contract between capability_registry and the LLM planner.

The planner reads registered capabilities to decide which KIOs to include
in a workflow sequence.
"""

from pydantic import BaseModel


class KIOCapability(BaseModel):
    """Describes what a KIO service can do."""

    kio_id: str  # e.g. "kio3"
    name: str  # e.g. "Repository Analyzer"
    description: str  # fed to LLM planner verbatim
    input_artifact_type: str | None  # None = accepts raw task payload
    output_artifact_type: str  # e.g. "RepoAnalysisArtifact"
    version: str = "1.0.0"
    tags: list[str] = []  # e.g. ["analysis", "repo", "bugs"]
    available: bool = True  # False = registered but unhealthy
    port: int | None = None  # HTTP port for health checks
