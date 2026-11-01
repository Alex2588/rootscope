"""FastAPI application factory."""

from fastapi import FastAPI

from app.api import health
from app.api.v1.router import router as v1_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name)

app.include_router(health.router)
app.include_router(v1_router, prefix="/api/v1")
