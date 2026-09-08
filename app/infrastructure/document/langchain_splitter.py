"""
app/infrastructure/document/langchain_splitter.py — LangChain splitter adapter

Implements TextSplitterPort using LangChain's RecursiveCharacterTextSplitter.
Respects paragraph/sentence boundaries instead of naive char slicing.

Falls back to sliding-window if LangChain not installed.
"""

from __future__ import annotations

import logging

from app.config import get_settings

logger = logging.getLogger("voice-doc-assistant")


class LangChainSplitter:
    """LangChain RecursiveCharacterTextSplitter adapter."""

    def __init__(self, chunk_size: int | None = None, chunk_overlap: int | None = None) -> None:
        settings = get_settings()
        self.chunk_size = chunk_size or settings.CHUNK_SIZE
        self.chunk_overlap = chunk_overlap or settings.CHUNK_OVERLAP

    def split(self, text: str) -> list[str]:
        if not text or not text.strip():
            return []

        # Try LangChain splitter — respects paragraphs/sentences
        try:
            from langchain_text_splitters import RecursiveCharacterTextSplitter  # type: ignore

            splitter = RecursiveCharacterTextSplitter(
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
                separators=["\n\n", "\n", " ", ""],
                length_function=len,
                is_separator_regex=False,
            )
            chunks = splitter.split_text(text)
            # LangChain returns list[str] directly
            if chunks:
                logger.info(f"LangChain splitter: {len(text)} chars -> {len(chunks)} chunks (size={self.chunk_size}, overlap={self.chunk_overlap})")
                return chunks
        except ImportError:
            logger.debug("langchain-text-splitters not installed, using fallback chunker")
        except Exception as exc:
            logger.warning(f"LangChain splitter failed: {exc}, falling back")

        # Fallback: naive sliding window (preserves previous behavior)
        return self._fallback_split(text)

    def _fallback_split(self, text: str, chunk_size: int | None = None, overlap: int | None = None) -> list[str]:
        cs = chunk_size or self.chunk_size
        ov = overlap or self.chunk_overlap
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
