"""
app/domain/entities.py — Core business entities

Pure Python dataclasses with no framework dependencies.
This is the innermost circle of Clean Architecture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Chunk:
    """Granular chunk with optional embedding — stored in vector DB."""

    id: str
    doc_id: str
    index: int
    text: str
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Document:
    """Core Document aggregate root."""

    id: str
    filename: str
    content_type: str
    size_bytes: int
    text: str
    uploaded_at: str  # ISO-8601
    uploaded_by: str  # user email
    chunks: list[str] = field(default_factory=list)
    # New: structured chunks + embeddings for vector store (kept optional for backwards compat)
    chunk_objects: list[Chunk] = field(default_factory=list)
    embeddings: list[list[float]] | None = None

    def to_metadata(self) -> dict[str, Any]:
        """Lightweight DTO for listing — no full text."""
        return {
            "id": self.id,
            "filename": self.filename,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "text_preview": self.text[:300],
            "text_length": len(self.text),
            "chunks": len(self.chunks),
            "uploaded_at": self.uploaded_at,
            "uploaded_by": self.uploaded_by,
        }
