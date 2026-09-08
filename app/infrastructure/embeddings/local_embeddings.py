"""
app/infrastructure/embeddings/local_embeddings.py — Factory + local fallback

Chooses embedding provider based on config. Prefers OpenAI when key present,
otherwise uses deterministic hashing (works offline, no API spend).
Supports sentence_transformers if installed and requested.
"""

from __future__ import annotations

import logging

from app.config import get_settings

logger = logging.getLogger("voice-doc-assistant")


def get_embedding_provider():
    """Factory — returns EmbeddingProvider per config."""
    settings = get_settings()
    provider = settings.EMBEDDING_PROVIDER.lower()

    if provider in ("sentence_transformers", "local"):
        try:
            from app.infrastructure.embeddings.sentence_transformer_embeddings import SentenceTransformerProvider  # type: ignore

            logger.info(f"Using SentenceTransformer embeddings ({settings.EMBEDDING_MODEL})")
            return SentenceTransformerProvider()
        except ImportError:
            logger.warning("sentence_transformers not installed, falling back to OpenAI/hashing")
        except Exception as exc:
            logger.warning(f"SentenceTransformer init failed: {exc}, falling back")

    # Default: OpenAI with hashing fallback (works without API key)
    from app.infrastructure.embeddings.openai_embeddings import OpenAIEmbeddingProvider

    return OpenAIEmbeddingProvider()


class LocalEmbeddingProvider:
    """Alias for hashing fallback — deterministic, no external deps."""

    async def embed(self, text: str) -> list[float]:
        from app.infrastructure.embeddings.openai_embeddings import OpenAIEmbeddingProvider

        # Use hashing path by not setting API key
        p = OpenAIEmbeddingProvider()
        # Force fallback by clearing client
        p._client = None
        # Monkey-patch to force hashing
        orig = p._get_client
        p._get_client = lambda: None  # type: ignore
        result = await p.embed(text)
        p._get_client = orig  # type: ignore
        return result

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        from app.infrastructure.embeddings.openai_embeddings import OpenAIEmbeddingProvider

        p = OpenAIEmbeddingProvider()
        p._client = None
        orig = p._get_client
        p._get_client = lambda: None  # type: ignore
        result = await p.embed_batch(texts)
        p._get_client = orig  # type: ignore
        return result
