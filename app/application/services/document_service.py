"""
app/application/services/document_service.py — Document use cases (LangChain + Vector)

Application service orchestrating document ingestion with LangChain
extraction/splitting and pgvector embedding. Keeps Clean Architecture:
depends on TextExtractor/TextSplitterPort via injection, not concrete libs.

Falls back gracefully if LangChain/embedding not configured — still works
on free tier without API spend.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, UploadFile

from app.config import get_settings
from app.domain.entities import Document
from app.tracing import traced_span

logger = logging.getLogger("voice-doc-assistant")
settings = get_settings()


class DocumentService:
    """Application service for document operations — LangChain + Vector."""

    def __init__(
        self,
        repository=None,
        extractor=None,
        splitter=None,
        embedding_provider=None,
        vector_repository=None,
    ) -> None:
        # Lazy imports to avoid circular deps — use factories
        if repository is None:
            from app.infrastructure.repositories.pgvector import pgvector_repository

            repository = pgvector_repository
        if extractor is None:
            try:
                from app.infrastructure.document.langchain_extractor import LangChainExtractor

                extractor = LangChainExtractor()
            except Exception:
                extractor = None
        if splitter is None:
            try:
                from app.infrastructure.document.langchain_splitter import LangChainSplitter

                splitter = LangChainSplitter()
            except Exception:
                splitter = None
        if embedding_provider is None:
            try:
                from app.infrastructure.embeddings import get_embedding_provider

                embedding_provider = get_embedding_provider()
            except Exception:
                embedding_provider = None
        if vector_repository is None:
            # pgvector_repository doubles as vector store
            try:
                from app.infrastructure.repositories.pgvector import pgvector_repository as vec_repo

                vector_repository = vec_repo
            except Exception:
                vector_repository = repository

        self.repo = repository
        self.extractor = extractor
        self.splitter = splitter
        self.embedding_provider = embedding_provider
        self.vector_repo = vector_repository
        self.max_upload_bytes = settings.max_upload_mb * 1024 * 1024

    async def upload(self, file: UploadFile, user) -> dict:
        with traced_span(
            "api.upload",
            attributes={
                "user.id": user.email,
                "file.name": file.filename or "unknown",
                "file.content_type": file.content_type or "unknown",
            },
        ) as span:
            if not file.filename:
                raise HTTPException(status_code=400, detail="Filename is required")

            data = await file.read()
            if len(data) == 0:
                raise HTTPException(status_code=400, detail="Uploaded file is empty")
            if len(data) > self.max_upload_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large: {len(data)} bytes > {self.max_upload_bytes} bytes ({settings.max_upload_mb} MB limit)",
                )
            if self.repo.count() >= settings.max_documents_in_memory:
                raise HTTPException(status_code=429, detail="Document store full — delete some documents first")

            # --- LangChain extraction (with fallback) ---
            text = self._extract_text(data, file.content_type or "application/octet-stream", file.filename)
            # --- LangChain splitting (with fallback) ---
            chunks = self._split_text(text)

            doc_id = uuid.uuid4().hex[:12]
            doc = Document(
                id=doc_id,
                filename=file.filename,
                content_type=file.content_type or "application/octet-stream",
                size_bytes=len(data),
                text=text,
                chunks=chunks,
                uploaded_at=datetime.now(timezone.utc).isoformat(),
                uploaded_by=user.email,
            )

            # Save to repository (memory + pgvector)
            self.repo.save(doc)
            self.repo.add_to_user_index(user.email, doc_id)

            # --- Vector embedding (LangChain + pgvector) ---
            # Embed chunks and save vectors — non-blocking for UX, but await for consistency
            embeddings: list[list[float]] | None = None
            if self.embedding_provider and chunks:
                try:
                    with traced_span("embedding.batch", attributes={"chunks": len(chunks), "model": settings.EMBEDDING_MODEL}) as emb_span:
                        embeddings = await self.embedding_provider.embed_batch(chunks)
                        emb_span.set_attribute("embeddings.count", len(embeddings))
                        emb_span.set_attribute("embedding.dim", len(embeddings[0]) if embeddings else 0)
                    doc.embeddings = embeddings
                    # Save vectors — handles pgvector vs in-memory
                    if hasattr(self.vector_repo, "save_with_embeddings"):
                        await self.vector_repo.save_with_embeddings(doc, embeddings)
                        logger.info(f"Vector store: saved {len(embeddings)} embeddings for doc {doc_id}")
                except Exception as exc:
                    logger.warning(f"Vector embedding failed for {doc_id}: {exc} — keyword search still available")
                    # Don't fail upload — vector is enhancement, not requirement
                    span.add_event("embedding.failed", {"error": str(exc)})

            span.set_attribute("document.id", doc_id)
            span.set_attribute("document.chunks", len(chunks))
            span.set_attribute("document.text_length", len(text))
            span.set_attribute("vector.enabled", embeddings is not None)

            return {
                "id": doc_id,
                "filename": file.filename,
                "size_bytes": len(data),
                "text_length": len(text),
                "chunks": len(chunks),
                "preview": text[:500],
                "uploaded_by": user.email,
                "vector": embeddings is not None,
                "embedding_model": settings.EMBEDDING_MODEL if embeddings else None,
            }

    def _extract_text(self, data: bytes, content_type: str, filename: str) -> str:
        if self.extractor:
            try:
                with traced_span("document.extract", attributes={"filename": filename, "content_type": content_type}):
                    text = self.extractor.extract(data, content_type, filename)
                    if text and len(text.strip()) > 20:
                        return text
            except Exception as exc:
                logger.warning(f"LangChain extractor failed for {filename}: {exc}, falling back to naive decode")
        # Fallback: naive decode (original logic)
        return self._fallback_extract(data, content_type, filename)

    def _fallback_extract(self, data: bytes, content_type: str, filename: str) -> str:
        import re

        try:
            text = data.decode("utf-8")
            if text.strip() and sum(c.isprintable() or c.isspace() for c in text) / max(len(text), 1) > 0.6:
                return text
        except Exception:
            pass
        try:
            text = data.decode("latin-1", errors="ignore")
            text = re.sub(r"[^\x20-\x7E\n\r\t\u00A0-\u024F\u0400-\u04FF\u4E00-\u9FFF\u0600-\u06FF]+", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > 30:
                return text
        except Exception:
            pass
        return f"[Document {filename} uploaded ({len(data)} bytes) — no readable text extracted]"

    def _split_text(self, text: str) -> list[str]:
        if self.splitter:
            try:
                with traced_span("document.split", attributes={"text_length": len(text)}):
                    chunks = self.splitter.split(text)
                    if chunks:
                        return chunks
            except Exception as exc:
                logger.warning(f"LangChain splitter failed: {exc}, falling back to naive")
        # Fallback: naive sliding window
        cs = settings.CHUNK_SIZE
        ov = settings.CHUNK_OVERLAP
        if len(text) <= cs:
            return [text]
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + cs
            chunks.append(text[start:end])
            start = end - ov
            if start < 0:
                start = 0
        return chunks

    def list_for_user(self, user) -> dict:
        if user.username == "guest":
            docs = self.repo.list_all()
        else:
            docs = self.repo.list_by_user(user.email)
        return {"documents": [d.to_metadata() for d in docs], "count": len(docs)}

    def delete(self, doc_id: str, user) -> dict:
        doc = self.repo.get(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        if user.username != "guest" and doc.uploaded_by != user.email:
            raise HTTPException(status_code=403, detail="Not authorized to delete this document")
        self.repo.delete(doc_id)
        self.repo.remove_from_user_index(doc_id)
        return {"deleted": doc_id}


# Singleton for DI — uses LangChain + pgvector when available, falls back to naive/memory
document_service = DocumentService()
