"""
app/infrastructure/repositories/memory.py — In-memory document store

Adapter implementing DocumentRepository.
Holds the global singletons (DOCUMENT_STORE / USER_DOCUMENTS) so that
existing code and tests keep working, but now behind an explicit interface.

In production, swap this file for a Postgres/pgvector implementation
without touching domain or application layers.
"""

from __future__ import annotations

from collections import defaultdict

from app.domain.entities import Document


# ---------------------------------------------------------------------------
# Global singletons — kept at module level for process-wide in-memory store
# ---------------------------------------------------------------------------
DOCUMENT_STORE: dict[str, Document] = {}
USER_DOCUMENTS: dict[str, list[str]] = defaultdict(list)


class InMemoryDocumentRepository:
    """In-memory adapter for DocumentRepository port."""

    # Core CRUD
    def save(self, doc: Document) -> None:
        DOCUMENT_STORE[doc.id] = doc

    def get(self, doc_id: str) -> Document | None:
        return DOCUMENT_STORE.get(doc_id)

    def delete(self, doc_id: str) -> None:
        DOCUMENT_STORE.pop(doc_id, None)

    def list_all(self) -> list[Document]:
        return list(DOCUMENT_STORE.values())

    def list_by_user(self, email: str) -> list[Document]:
        ids = USER_DOCUMENTS.get(email, [])
        return [DOCUMENT_STORE[did] for did in ids if did in DOCUMENT_STORE]

    def count(self) -> int:
        return len(DOCUMENT_STORE)

    # User index
    def add_to_user_index(self, email: str, doc_id: str) -> None:
        USER_DOCUMENTS[email].append(doc_id)

    def remove_from_user_index(self, doc_id: str) -> None:
        for uid in list(USER_DOCUMENTS.keys()):
            if doc_id in USER_DOCUMENTS[uid]:
                USER_DOCUMENTS[uid].remove(doc_id)

    def get_user_doc_ids(self, email: str) -> list[str]:
        return list(USER_DOCUMENTS.get(email, []))

    # Helpers for testing / maintenance
    def clear(self) -> None:
        DOCUMENT_STORE.clear()
        USER_DOCUMENTS.clear()


# Singleton instance used via dependency injection
document_repository = InMemoryDocumentRepository()
