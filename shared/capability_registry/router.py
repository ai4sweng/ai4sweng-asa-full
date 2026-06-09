"""
Capability Registry HTTP endpoints — queried by orchestrator and dashboard.
"""

from fastapi import APIRouter, Depends

from shared.capability_registry.registry import CapabilityRegistry, get_capability_registry
from shared.contracts.capabilities import KIOCapability

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


@router.get("", response_model=list[KIOCapability])
async def list_capabilities(
    available_only: bool = True,
    registry: CapabilityRegistry = Depends(get_capability_registry),
) -> list[KIOCapability]:
    """List all registered KIO capabilities. Used by the LLM planner and dashboard."""
    return await registry.list_capabilities(available_only=available_only)


@router.get("/planner-context")
async def planner_context(
    registry: CapabilityRegistry = Depends(get_capability_registry),
) -> dict:
    """Return capabilities formatted as an LLM prompt block."""
    context = await registry.build_planner_context()
    return {"context": context}
