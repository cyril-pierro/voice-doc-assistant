"""
app/application/services/retrieval_service.py — Retrieval / tool use case (Vector + Hybrid)

Implements `query_document` — the function the LLM calls during realtime generation.
Now vector-aware: uses pgvector cosine similarity with hybrid BM25 fallback.
Keeps Clean Architecture: depends on EmbeddingProvider + VectorRepository ports.

Traced via OpenInference conventions.
"""

from __future__ import annotations

import re
import time
from typing import Any

from app.config import get_settings
from app.tracing import traced_span

QUERY_DOCUMENT_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "name": "query_document",
    "description": (
        "Search the user's uploaded documents for relevant context. "
        "Use this whenever the user asks about their documents, files, or any factual content that might be in the uploaded context. "
        "Always call this before answering document-related questions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query derived from user intent",
            }
        },
        "required": ["query"],
    },
}

settings = get_settings()


class RetrievalService:
    """Application service for document-grounded retrieval — vector + hybrid."""

    def __init__(self, embedding_provider=None, vector_repository=None) -> None:
        # Lazy — supports offline without API key
        self._embedding_provider = embedding_provider
        self._vector_repo = vector_repository

    def _get_embedding_provider(self):
        if self._embedding_provider is not None:
            return self._embedding_provider
        try:
            from app.infrastructure.embeddings import get_embedding_provider

            self._embedding_provider = get_embedding_provider()
            return self._embedding_provider
        except Exception:
            return None

    def _get_vector_repo(self):
        if self._vector_repo is not None:
            return self._vector_repo
        try:
            from app.infrastructure.repositories.pgvector import pgvector_repository

            self._vector_repo = pgvector_repository
            return self._vector_repo
        except Exception:
            # Fall back to memory
            from app.infrastructure.repositories.memory import document_repository

            return document_repository

    async def query(self, query: str, user_id: str | None = None) -> str:
        """
        Vector-aware retrieval — tries pgvector cosine first, falls back to keyword.
        Returns formatted context string for the LLM.
        """
        with traced_span(
            "tool.query_document",
            attributes={
                "tool.name": "query_document",
                "tool.query": query,
                "user.id": user_id or "anonymous",
                "retrieval.top_k": settings.VECTOR_TOP_K,
            },
        ) as span:
            start = time.perf_counter()

            # --- Try vector search first (semantic, multilingual) ---
            vector_results = await self._vector_search(query, user_id, span)
            if vector_results:
                latency_ms = (time.perf_counter() - start) * 1000
                span.set_attribute("retrieval.latency_ms", latency_ms)
                span.set_attribute("retrieval.result_count", len(vector_results))
                span.set_attribute("retrieval.mode", "vector")
                context_blocks = []
                for i, (chunk, filename, sim) in enumerate(vector_results, 1):
                    context_blocks.append(f"[Source {i} | {filename} | similarity={sim:.3f}]\n{chunk.strip()}")
                result = "\n\n---\n\n".join(context_blocks)
                span.set_attribute("retrieval.context_chars", len(result))
                span.add_event("retrieval.completed", {"result_preview": result[:500], "mode": "vector"})
                return result

            # --- Fallback: keyword scoring (offline, no embeddings) ---
            # Hybrid path: if HYBRID_ALPHA < 1, blend vector + keyword; for now pure fallback
            result = self._keyword_search(query, user_id, span, start)
            span.set_attribute("retrieval.mode", "keyword_fallback")
            return result

    async def _vector_search(self, query: str, user_id: str | None, span) -> list[tuple[str, str, float]] | None:
        """Try pgvector cosine search — returns None if not available or no hits."""
        try:
            provider = self._get_embedding_provider()
            repo = self._get_vector_repo()
            if not provider or not hasattr(repo, "search"):
                return None

            # Embed query
            with traced_span("embedding.query", attributes={"query": query[:200], "model": settings.EMBEDDING_MODEL}):
                query_embedding = await provider.embed(query)

            # Search vector store
            with traced_span("vector.search", attributes={"top_k": settings.VECTOR_TOP_K, "user_id": user_id or "anonymous"}):
                results = await repo.search(
                    query_embedding=query_embedding,
                    user_id=user_id,
                    top_k=settings.VECTOR_TOP_K,
                    threshold=settings.VECTOR_THRESHOLD,
                )
                if results:
                    span.set_attribute("vector.hits", len(results))
                    span.set_attribute("vector.top_similarity", results[0][2] if results else 0)
                    return results
        except Exception as exc:
            span.add_event("vector.search_failed", {"error": str(exc)})
        return None

    def _keyword_search(self, query: str, user_id: str | None, span, start: float) -> str:
        """Original keyword scorer — kept as fallback and for hybrid blending."""
        import re as _re

        from app.infrastructure.repositories.memory import DOCUMENT_STORE, USER_DOCUMENTS, document_repository

        query_lower = query.lower()
        query_tokens = set(_re.findall(r"\w+", query_lower))

        if user_id and user_id in USER_DOCUMENTS:
            candidate_ids = USER_DOCUMENTS[user_id]
        else:
            candidate_ids = list(DOCUMENT_STORE.keys())

        if not candidate_ids:
            span.set_attribute("retrieval.result_count", 0)
            return "No documents have been uploaded yet. Ask the user to upload a document first."

        scored: list[tuple[int, str, str]] = []
        for doc_id in candidate_ids:
            doc = document_repository.get(doc_id)
            if not doc:
                continue
            for chunk in doc.chunks:
                chunk_tokens = set(_re.findall(r"\w+", chunk.lower()))
                overlap = len(query_tokens & chunk_tokens)
                substring_bonus = 2 if query_lower in chunk.lower() else 0
                score = overlap * 2 + substring_bonus
                if overlap > 0 or substring_bonus:
                    scored.append((score, chunk, doc.filename))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[: settings.VECTOR_TOP_K]

        latency_ms = (time.perf_counter() - start) * 1000
        # Don't overwrite if already set by vector path
        try:
            span.set_attribute("retrieval.latency_ms", latency_ms)
            span.set_attribute("retrieval.result_count", len(top))
        except Exception:
            pass

        if not top:
            return (
                f"No relevant context found for query '{query}' across {len(candidate_ids)} document(s). "
                "Inform the user that their documents don't contain matching information."
            )

        context_blocks = []
        for i, (score, chunk, filename) in enumerate(top, 1):
            context_blocks.append(f"[Source {i} | {filename} | score={score}]\n{chunk.strip()}")

        result = "\n\n---\n\n".join(context_blocks)
        try:
            span.set_attribute("retrieval.context_chars", len(result))
            span.add_event("retrieval.completed", {"result_preview": result[:500], "mode": "keyword"})
        except Exception:
            pass
        return result

    # Sync wrapper for backwards compat (non-async callers)
    def query_sync(self, query: str, user_id: str | None = None) -> str:
        """Sync wrapper — creates event loop if needed."""
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If already in event loop (e.g., FastAPI), create task
                # For sync callers, we fallback to keyword directly to avoid deadlock
                return self._keyword_search_sync(query, user_id)
            return loop.run_until_complete(self.query(query, user_id))
        except RuntimeError:
            return self._keyword_search_sync(query, user_id)

    def _keyword_search_sync(self, query: str, user_id: str | None) -> str:
        """Sync keyword fallback for backwards compat."""
        import time as _time

        # Minimal span-less version to avoid async tracing issues
        query_lower = query.lower()
        query_tokens = set(re.findall(r"\w+", query_lower))
        from app.infrastructure.repositories.memory import DOCUMENT_STORE, USER_DOCUMENTS, document_repository

        if user_id and user_id in USER_DOCUMENTS:
            candidate_ids = USER_DOCUMENTS[user_id]
        else:
            candidate_ids = list(DOCUMENT_STORE.keys())
        if not candidate_ids:
            return "No documents have been uploaded yet. Ask the user to upload a document first."
        scored = []
        for doc_id in candidate_ids:
            doc = document_repository.get(doc_id)
            if not doc:
                continue
            for chunk in doc.chunks:
                chunk_tokens = set(re.findall(r"\w+", chunk.lower()))
                overlap = len(query_tokens & chunk_tokens)
                substring_bonus = 2 if query_lower in chunk.lower() else 0
                score = overlap * 2 + substring_bonus
                if overlap > 0 or substring_bonus:
                    scored.append((score, chunk, doc.filename))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[: settings.VECTOR_TOP_K]
        if not top:
            return f"No relevant context found for query '{query}' across {len(candidate_ids)} document(s)."
        return "\n\n---\n\n".join(f"[Source {i} | {filename} | score={score}]\n{chunk.strip()}" for i, (score, chunk, filename) in enumerate(top, 1))


# Singleton for DI
retrieval_service = RetrievalService()


# Backwards-compatible exports
def query_document(query: str, user_id: str | None = None) -> str:
    """Sync entry point kept for `from app.main import query_document`."""
    return retrieval_service._keyword_search_sync(query, user_id=user_id)


async def query_document_async(query: str, user_id: str | None = None) -> str:
    """Async entry point for new vector-aware callers."""
    return await retrieval_service.query(query, user_id=user_id)
