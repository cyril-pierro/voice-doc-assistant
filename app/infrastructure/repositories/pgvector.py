"""
app/infrastructure/repositories/pgvector.py — Vector repository (pgvector + in-memory fallback)

Primary DB choice per user request: **pgvector (Postgres)** — production grade.
Falls back to **in-memory FAISS-like cosine store** when VECTOR_DB_URL not set,
so the prototype works on free tier without Postgres.

Implements DocumentRepository port with vector extension:
  - save_with_embeddings(doc, embeddings)
  - search(query_embedding, user_id, top_k, threshold)

Clean Architecture: only this file knows about Postgres/pgvector vs memory.
Application layer depends on DocumentRepository Protocol, not this class.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from collections import defaultdict

from app.config import get_settings
from app.domain.entities import Chunk, Document
from app.infrastructure.repositories.memory import document_repository as memory_repo
from app.infrastructure.repositories.memory import DOCUMENT_STORE, USER_DOCUMENTS

logger = logging.getLogger("voice-doc-assistant")

# ------------------------------------------------------------------ #
# In-memory vector store (fallback) — cosine similarity
# ------------------------------------------------------------------ #
# Structure: {chunk_id: {text, embedding, filename, user_email, doc_id, chunk_index}}
_IN_MEMORY_VECTORS: dict[str, dict] = {}
# Per-user index for fast filtering
_USER_VECTOR_IDS: dict[str, list[str]] = defaultdict(list)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class PgVectorRepository:
    """
    Vector repository — pgvector when VECTOR_DB_URL set, else in-memory.

    Table schema when using Postgres (created lazily):
      documents(id TEXT PRIMARY KEY, filename TEXT, user_email TEXT, text TEXT, created_at TIMESTAMPTZ)
      chunks(id TEXT PRIMARY KEY, doc_id TEXT, user_email TEXT, chunk_index INT, text TEXT, embedding vector(1536))
      Index: CREATE INDEX ON chunks USING ivfflat (embedding vector_cosine_ops)
    """

    def __init__(self, dsn: str | None = None) -> None:
        settings = get_settings()
        self.dsn = dsn or settings.vector_db_url
        self.use_pgvector = bool(self.dsn)
        self._pool = None
        if self.use_pgvector:
            logger.info(f"PgVectorRepository: will use Postgres at {self.dsn[:30]}... (vector)")
        else:
            logger.info("PgVectorRepository: no VECTOR_DB_URL — using in-memory vectors (free tier)")

    # ------------------------------------------------------------------ #
    # Postgres helpers — lazy connect
    # ------------------------------------------------------------------ #
    async def _get_pool(self):
        if not self.use_pgvector or not self.dsn:
            return None
        if self._pool is not None:
            return self._pool
        try:
            import asyncpg  # type: ignore

            self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
            # Ensure tables + pgvector extension
            async with self._pool.acquire() as conn:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS documents (
                        id TEXT PRIMARY KEY,
                        filename TEXT,
                        user_email TEXT,
                        text TEXT,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                # Use dynamic dim from settings
                dim = get_settings().EMBEDDING_DIM
                await conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS chunks (
                        id TEXT PRIMARY KEY,
                        doc_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
                        user_email TEXT,
                        chunk_index INT,
                        text TEXT,
                        embedding vector({dim})
                    )
                """)
                # Index for cosine similarity
                try:
                    await conn.execute("CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)")
                except Exception:
                    # ivfflat requires data — skip if empty
                    pass
            logger.info("PgVector tables ensured")
            return self._pool
        except ImportError:
            logger.warning("asyncpg not installed — falling back to in-memory vectors")
            self.use_pgvector = False
            return None
        except Exception as exc:
            logger.warning(f"PgVector connect failed ({exc}) — falling back to in-memory")
            self.use_pgvector = False
            return None

    # ------------------------------------------------------------------ #
    # DocumentRepository interface — delegates to memory_repo for CRUD,
    # but adds vector handling for save_with_embeddings / search
    # ------------------------------------------------------------------ #
    def save(self, doc: Document) -> None:
        # Keep memory store in sync for list_all etc (fast, no DB roundtrip)
        memory_repo.save(doc)

    def get(self, doc_id: str) -> Document | None:
        return memory_repo.get(doc_id)

    def delete(self, doc_id: str) -> None:
        memory_repo.delete(doc_id)
        # Also remove vectors
        to_delete = [cid for cid, v in _IN_MEMORY_VECTORS.items() if v["doc_id"] == doc_id]
        for cid in to_delete:
            data = _IN_MEMORY_VECTORS.pop(cid, None)
            if data:
                email = data.get("user_email")
                if email and cid in _USER_VECTOR_IDS[email]:
                    _USER_VECTOR_IDS[email].remove(cid)
        # Postgres: handled via ON DELETE CASCADE, but also delete explicitly
        if self.use_pgvector and self._pool:
            # Fire and forget — don't block
            import asyncio

            async def _del():
                pool = await self._get_pool()
                if pool:
                    async with pool.acquire() as conn:
                        await conn.execute("DELETE FROM chunks WHERE doc_id = $1", doc_id)
                        await conn.execute("DELETE FROM documents WHERE id = $1", doc_id)

            try:
                asyncio.create_task(_del())
            except Exception:
                pass

    def list_all(self) -> list[Document]:
        return memory_repo.list_all()

    def list_by_user(self, email: str) -> list[Document]:
        return memory_repo.list_by_user(email)

    def count(self) -> int:
        return memory_repo.count()

    def add_to_user_index(self, email: str, doc_id: str) -> None:
        memory_repo.add_to_user_index(email, doc_id)

    def remove_from_user_index(self, doc_id: str) -> None:
        memory_repo.remove_from_user_index(doc_id)

    def get_user_doc_ids(self, email: str) -> list[str]:
        return memory_repo.get_user_doc_ids(email)

    # ------------------------------------------------------------------ #
    # Vector extension
    # ------------------------------------------------------------------ #
    async def save_with_embeddings(self, doc: Document, embeddings: list[list[float]]) -> None:
        """Save doc + embeddings — in-memory and optionally pgvector."""
        # Always keep memory store
        self.save(doc)
        # In-memory vectors
        for idx, (chunk_text, emb) in enumerate(zip(doc.chunks, embeddings)):
            chunk_id = f"{doc.id}_{idx}_{uuid.uuid4().hex[:4]}"
            _IN_MEMORY_VECTORS[chunk_id] = {
                "id": chunk_id,
                "doc_id": doc.id,
                "chunk_index": idx,
                "text": chunk_text,
                "embedding": emb,
                "filename": doc.filename,
                "user_email": doc.uploaded_by,
            }
            _USER_VECTOR_IDS[doc.uploaded_by].append(chunk_id)

        # Postgres — async write
        pool = await self._get_pool()
        if pool:
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO documents (id, filename, user_email, text) VALUES ($1,$2,$3,$4) ON CONFLICT (id) DO NOTHING",
                        doc.id,
                        doc.filename,
                        doc.uploaded_by,
                        doc.text,
                    )
                    for idx, (chunk_text, emb) in enumerate(zip(doc.chunks, embeddings)):
                        chunk_id = f"{doc.id}_{idx}"
                        # pgvector expects string "[0.1,0.2,...]" or list
                        emb_str = json.dumps(emb)
                        await conn.execute(
                            "INSERT INTO chunks (id, doc_id, user_email, chunk_index, text, embedding) VALUES ($1,$2,$3,$4,$5,$6::vector) ON CONFLICT (id) DO UPDATE SET text=$5, embedding=$6::vector",
                            chunk_id,
                            doc.id,
                            doc.uploaded_by,
                            idx,
                            chunk_text,
                            emb_str,
                        )
                logger.info(f"PgVector saved {len(embeddings)} vectors for doc {doc.id}")
            except Exception as exc:
                logger.warning(f"PgVector save failed ({exc}) — in-memory vectors still available")

    async def search(
        self, query_embedding: list[float], user_id: str | None, top_k: int = 3, threshold: float | None = None
    ) -> list[tuple[str, str, float]]:
        """
        Vector search — returns (chunk_text, filename, similarity).
        Uses pgvector if configured, else in-memory cosine.
        Respects user_id filtering and threshold.
        """
        # Try pgvector first if available
        pool = await self._get_pool()
        if pool and self.use_pgvector:
            try:
                async with pool.acquire() as conn:
                    # Use cosine similarity: 1 - (embedding <=> query)
                    # pgvector <=> is cosine distance
                    if user_id:
                        rows = await conn.fetch(
                            """
                            SELECT text, filename, 1 - (embedding <=> $1::vector) AS similarity
                            FROM chunks
                            WHERE user_email = $2
                            ORDER BY embedding <=> $1::vector
                            LIMIT $3
                            """,
                            json.dumps(query_embedding),
                            user_id,
                            top_k,
                        )
                    else:
                        rows = await conn.fetch(
                            """
                            SELECT text, filename, 1 - (embedding <=> $1::vector) AS similarity
                            FROM chunks
                            ORDER BY embedding <=> $1::vector
                            LIMIT $2
                            """,
                            json.dumps(query_embedding),
                            top_k,
                        )
                    results = [(r["text"], r["filename"], float(r["similarity"])) for r in rows]
                    if threshold is not None:
                        results = [r for r in results if r[2] >= threshold]
                    if results:
                        logger.info(f"PgVector search user={user_id} top_k={top_k} -> {len(results)} hits")
                        return results
                    # Fall through to in-memory if no hits
            except Exception as exc:
                logger.warning(f"PgVector search failed ({exc}), falling back to in-memory")

        # In-memory fallback — cosine over _IN_MEMORY_VECTORS
        candidates: list[tuple[float, str, str]] = []
        # Filter by user
        if user_id and user_id in _USER_VECTOR_IDS:
            ids = _USER_VECTOR_IDS[user_id]
        elif user_id:
            ids = []  # user has no vectors
        else:
            ids = list(_IN_MEMORY_VECTORS.keys())

        for cid in ids:
            data = _IN_MEMORY_VECTORS.get(cid)
            if not data or not data.get("embedding"):
                continue
            sim = _cosine_similarity(query_embedding, data["embedding"])
            if threshold is not None and sim < threshold:
                continue
            candidates.append((sim, data["text"], data["filename"]))

        candidates.sort(key=lambda x: x[0], reverse=True)
        top = candidates[:top_k]
        # Return as (text, filename, similarity)
        return [(text, filename, sim) for sim, text, filename in top]


# Singleton — auto-selects pgvector vs memory based on VECTOR_DB_URL
pgvector_repository = PgVectorRepository()

# Backwards-compat alias — so `from app.infrastructure.repositories.pgvector import document_repository` works
document_repository = pgvector_repository
