"""HTTP route modules."""

from app.presentation.routes.documents import router as documents_router
from app.presentation.routes.health import router as health_router

__all__ = ["health_router", "documents_router"]
