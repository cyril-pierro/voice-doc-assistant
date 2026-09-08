"""Domain layer — pure business entities and repository contracts.

No dependencies on FastAPI, infrastructure, or external services.
"""

from app.domain.entities import Document

__all__ = ["Document"]
