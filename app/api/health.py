"""Infrastructure health checks — unversioned, outside the /api/v1 contract.

Deliberately dependency-free: liveness/readiness must not call the model, the database,
or anything else that could make the probe itself a failure point.
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    """Process liveness: the process is up and serving requests."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready() -> dict[str, str]:
    """Readiness for accepting work.

    Placeholder until dependencies exist (DB, worker queue) — will check those once
    they're introduced.
    """
    return {"status": "ok"}
