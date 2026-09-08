"""
app/presentation/routes/health.py — Health check endpoint
"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.infrastructure.repositories.memory import document_repository

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Liveness probe — used by Render / HF Spaces health checks."""
    settings = get_settings()  # fresh per-request for env changes
    return {
        "status": "ok",
        "service": settings.project_name,
        "version": settings.project_version,
        "provider": settings.resolved_provider,
        "documents": document_repository.count(),
        "tracing": settings.effective_tracing_enabled,
    }
