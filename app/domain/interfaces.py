"""
app/domain/interfaces.py — Repository contracts (ports)

Clean Architecture: domain defines *what* it needs,
infrastructure decides *how* to provide it (Dependency Inversion).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol

from app.domain.entities import Chunk, Document


class TextExtractor(Protocol):
    """Port for document text extraction (LangChain loaders)."""

    def extract(self, data: bytes, content_type: str, filename: str) -> str: ...


class TextSplitterPort(Protocol):
    """Port for semantic chunking (LangChain RecursiveCharacterTextSplitter)."""

    def split(self, text: str) -> list[str]: ...


class EmbeddingProvider(Protocol):
    """Port for embedding generation."""

    async def embed(self, text: str) -> list[float]: ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class DocumentRepository(Protocol):
    """Port for document persistence — extended for vector search."""

    def save(self, doc: Document) -> None: ...
    def get(self, doc_id: str) -> Document | None: ...
    def delete(self, doc_id: str) -> None: ...
    def list_all(self) -> list[Document]: ...
    def list_by_user(self, email: str) -> list[Document]: ...
    def count(self) -> int: ...
    def add_to_user_index(self, email: str, doc_id: str) -> None: ...
    def remove_from_user_index(self, doc_id: str) -> None: ...
    def get_user_doc_ids(self, email: str) -> list[str]: ...

    # Vector extension — optional for in-memory, required for pgvector
    async def save_with_embeddings(self, doc: Document, embeddings: list[list[float]]) -> None: ...  # noqa: E704
    async def search(
        self, query_embedding: list[float], user_id: str | None, top_k: int = 3, threshold: float | None = None
    ) -> list[tuple[str, str, float]]: ...  # (chunk_text, filename, similarity)


class RealtimeProvider(ABC):
    """Port for realtime voice upstream providers."""

    @abstractmethod
    async def handle(self, client_ws, user) -> None:
        """Bridge browser WS <-> upstream provider."""
        raise NotImplementedError
