"""Aggregates all v1 business endpoints.

Mounted once, under /api/v1, in app.main. Individual routers (e.g. investigations) are
included here as they're built — this stays the single place that knows what v1 contains.
"""

from fastapi import APIRouter

router = APIRouter()

# router.include_router(investigations.router)  # added when investigations lands (G1)
