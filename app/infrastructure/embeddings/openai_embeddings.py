"""
app/infrastructure/embeddings/openai_embeddings.py — OpenAI embedding adapter

Implements EmbeddingProvider using text-embedding-3-small (1536d).
Falls back to local hashing embeddings when OPENAI_API_KEY not set,
so vector workflow works offline / on free tier without API spend.
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Any

from app.config import get_settings

logger = logging.getLogger("voice-doc-assistant")


class OpenAIEmbeddingProvider:
    """OpenAI text-embedding-3-small — 1536 dim, multilingual-aware."""

    def __init__(self, model: str | None = None, dim: int | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.EMBEDDING_MODEL
        self.dim = dim or settings.EMBEDDING_DIM
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI  # type: ignore

            api_key = get_settings().OPENAI_API_KEY
            if not api_key:
                return None
            self._client = OpenAI(api_key=api_key)
            return self._client
        except ImportError:
            logger.debug("openai not installed, using local embeddings")
            return None
        except Exception as exc:
            logger.warning(f"OpenAI client init failed: {exc}")
            return None

    async def embed(self, text: str) -> list[float]:
        return (await self.embed_batch([text]))[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        client = self._get_client()
        if client is not None:
            try:
                # OpenAI embeddings are sync — run in thread pool for async compat
                import asyncio

                def _call():
                    resp = client.embeddings.create(model=self.model, input=texts)
                    return [d.embedding for d in resp.data]

                embeddings = await asyncio.to_thread(_call)
                logger.info(f"OpenAI embeddings: {len(texts)} texts -> {len(embeddings)} vectors ({self.dim}d via {self.model})")
                return embeddings
            except Exception as exc:
                logger.warning(f"OpenAI embedding failed ({exc}), falling back to local hashing")

        # Fallback: deterministic hashing embeddings (offline, free, multilingual via char ngrams)
        return [self._hash_embed(t) for t in texts]

    def _hash_embed(self, text: str) -> list[float]:
        """Deterministic char-ngram hashing — 1536d, L2-normalized, works offline."""
        # Use multiple hash seeds to fill dim
        vec = [0.0] * self.dim
        # Char bigrams + word tokens
        tokens = text.lower().split()
        # Also char trigrams for CJK
        chars = [c for c in text.lower() if not c.isspace()]
        ngrams = ["".join(chars[i : i + 3]) for i in range(max(0, len(chars) - 2))] if chars else []
        all_tokens = tokens + ngrams
        if not all_tokens:
            all_tokens = [text.lower()]

        for token in all_tokens:
            # Two hashes per token for better distribution
            h1 = int(hashlib.md5(token.encode()).hexdigest(), 16)
            h2 = int(hashlib.sha256(token.encode()).hexdigest(), 16)
            idx1 = h1 % self.dim
            idx2 = h2 % self.dim
            vec[idx1] += 1.0
            vec[idx2] += 0.5

        # L2 normalize
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec
