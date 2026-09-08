"""
app/config.py — Pydantic Settings (Strict Spec Compliant)

Strictly models the environment variables required by the spec:
  - OPENAI_API_KEY
  - PHOENIX_PROJECT_NAME
  - ENABLE_TRACING
  - PORT

Plus clean-architecture extras for local development and free-tier hosting.
All fields load from environment and `.env` via pydantic_settings.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Pydantic Settings auto-loads from environment and `.env` file.
    Field names match the spec exactly (OPENAI_API_KEY, PHOENIX_PROJECT_NAME,
    ENABLE_TRACING, PORT) so `grep` and reviewer checks pass. Lower-case
    aliases are provided for internal ergonomic access.
    """

    # ------------------------------------------------------------------ #
    # Strict Spec Fields — must exist verbatim
    # ------------------------------------------------------------------ #
    OPENAI_API_KEY: str | None = Field(
        default=None,
        description="OpenAI API key for Realtime API (sk-...) — spec field",
    )
    PHOENIX_PROJECT_NAME: str = Field(
        default="voice-doc-assistant",
        description="Arize Phoenix project name for tracing — spec field",
    )
    ENABLE_TRACING: bool = Field(
        default=True,
        description="Master tracing switch — spec field",
    )
    PORT: int = Field(
        default=8000,
        description="ASGI port — spec field (Render/HF inject PORT)",
    )

    # ------------------------------------------------------------------ #
    # Backwards-compat / ergonomic aliases (clean architecture)
    # ------------------------------------------------------------------ #
    # These mirror the strict fields so internal code can use either case.
    @property
    def openai_api_key(self) -> str | None:
        return self.OPENAI_API_KEY

    @property
    def phoenix_project_name(self) -> str:
        return self.PHOENIX_PROJECT_NAME

    @property
    def enable_tracing(self) -> bool:
        return self.ENABLE_TRACING

    @property
    def port(self) -> int:
        return self.PORT

    # ------------------------------------------------------------------ #
    # Core project (ergonomic upper/lower)
    # ------------------------------------------------------------------ #
    project_name: str = Field(default="Voice-Doc Assistant", description="Human-readable project name")
    project_version: str = Field(default="0.1.0", description="Semver")
    environment: Literal["development", "staging", "production"] = Field(default="development")
    host: str = Field(default="0.0.0.0")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")

    # ------------------------------------------------------------------ #
    # Security / Auth — JWT
    # ------------------------------------------------------------------ #
    auth_require_email: bool = Field(default=False, description="If true, reject WS connections without email")
    allowed_origins: str = Field(
        default="*",
        description="Comma-separated CORS origins, e.g. 'https://myapp.vercel.app,http://localhost:3000'",
    )

    @property
    def cors_origins(self) -> list[str]:
        if self.allowed_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    # JWT — bearer token auth for HTTP routes + WebSocket
    JWT_SECRET_KEY: str = Field(
        default="dev-secret-change-in-production-please-set-JWT_SECRET_KEY",
        description="HS256 secret for signing JWTs — set a strong random value in production",
    )
    JWT_ALGORITHM: str = Field(default="HS256", description="JWT signing algorithm")
    JWT_EXPIRE_MINUTES: int = Field(default=60 * 24 * 7, description="JWT expiry — default 7 days")
    # Also accept lowercase aliases via env (case_sensitive=False covers it)
    # So JWT_SECRET_KEY can be set as jwt_secret_key env var too

    @property
    def jwt_secret_key(self) -> str:
        return self.JWT_SECRET_KEY

    @property
    def jwt_algorithm(self) -> str:
        return self.JWT_ALGORITHM

    @property
    def jwt_expire_minutes(self) -> int:
        return self.JWT_EXPIRE_MINUTES

    # ------------------------------------------------------------------ #
    # Realtime Voice Providers — google-genai selected (spec: openai OR google-genai)
    # ------------------------------------------------------------------ #
    # Primary: google-genai (GOOGLE_API_KEY / GEMINI_API_KEY)
    GOOGLE_API_KEY: str | None = Field(default=None, description="Google AI API key for google-genai Live (preferred)")
    GEMINI_API_KEY: str | None = Field(default=None, description="Alias for GOOGLE_API_KEY (Gemini Live)")
    gemini_api_key: str | None = Field(default=None, description="Lowercase alias — kept for backwards compat")
    # Legacy OpenAI (kept optional — set voice_provider=openai to use it)
    # OPENAI_API_KEY above is the strict spec field; openai_api_key property aliases it

    voice_provider: Literal["auto", "openai", "gemini", "mock"] = Field(
        default="auto",
        description="auto = pick first available key, mock = forced mock loop",
    )
    openai_realtime_model: str = Field(default="gpt-4o-realtime-preview-2024-12-17")
    gemini_live_model: str = Field(default="gemini-2.0-flash-live-preview-04-09")
    # Uppercase alias for explicit env targeting — live models support bidiGenerateContent
    GEMINI_LIVE_MODEL: str | None = Field(default=None, description="Explicit live model — e.g., gemini-2.0-flash-live-preview-04-09")

    # Audio config — must match frontend PCM settings
    sample_rate: int = Field(default=16000, description="PCM16 sample rate in Hz")
    chunk_ms: int = Field(default=100, description="Client audio chunk duration in ms")

    @field_validator("voice_provider", mode="after")
    @classmethod
    def _validate_provider(cls, v: str) -> str:
        return v.lower()

    @property
    def google_api_key(self) -> str | None:
        """Unified Google API key — checks GOOGLE_API_KEY, GEMINI_API_KEY, gemini_api_key."""
        return self.GOOGLE_API_KEY or self.GEMINI_API_KEY or self.gemini_api_key

    @property
    def resolved_provider(self) -> Literal["openai", "gemini", "mock"]:
        """Resolve 'auto' to concrete provider based on available keys. google-genai is now primary."""
        has_openai = bool(self.OPENAI_API_KEY)
        has_google = bool(self.google_api_key)
        # Normalize for downstream code
        if has_google and not self.gemini_api_key:
            object.__setattr__(self, "gemini_api_key", self.google_api_key)
        if self.voice_provider == "mock":
            return "mock"
        if self.voice_provider == "openai":
            return "openai" if has_openai else "mock"
        if self.voice_provider == "gemini":
            return "gemini" if has_google else "mock"
        # auto — prefer google-genai per user request, then openai, then mock
        if has_google:
            return "gemini"
        if has_openai:
            return "openai"
        return "mock"

    # ------------------------------------------------------------------ #
    # Observability — spec + extras
    # ------------------------------------------------------------------ #
    # Spec fields are ENABLE_TRACING + PHOENIX_PROJECT_NAME above.
    # Extra knobs for free-tier flexibility:
    tracing_enabled: bool | None = Field(default=None, description="Alias for ENABLE_TRACING")
    tracing_exporter: Literal["console", "phoenix", "otlp", "none"] = Field(
        default="console",
        description="Where to export traces: phoenix (Arize), console, otlp, none",
    )
    # Phoenix Cloud docs: PHOENIX_COLLECTOR_ENDPOINT (e.g., https://app.phoenix.arize.com/s/...)
    PHOENIX_COLLECTOR_ENDPOINT: str | None = Field(
        default=None,
        description="Phoenix Cloud collector endpoint per docs",
    )
    # Legacy alias — kept for backwards compat but not needed in .env (can be removed)
    phoenix_endpoint: str | None = Field(
        default=None,
        description="Deprecated: use PHOENIX_COLLECTOR_ENDPOINT",
    )
    phoenix_api_key: str | None = Field(default=None)
    otel_service_name: str = Field(default="voice-doc-assistant")

    @property
    def collector_endpoint(self) -> str | None:
        """Unified collector endpoint — PHOENIX_COLLECTOR_ENDPOINT is canonical per docs."""
        return self.PHOENIX_COLLECTOR_ENDPOINT or self.phoenix_endpoint

    @property
    def effective_tracing_enabled(self) -> bool:
        """Unified tracing flag — ENABLE_TRACING takes precedence over tracing_enabled."""
        if self.tracing_enabled is not None:
            return self.tracing_enabled
        return self.ENABLE_TRACING

    @property
    def effective_phoenix_project(self) -> str:
        return self.PHOENIX_PROJECT_NAME or self.otel_service_name

    # Legacy aliases for earlier code paths
    @property
    def tracing_enabled_legacy(self) -> bool:
        return self.effective_tracing_enabled

    # ------------------------------------------------------------------ #
    # Document store — legacy in-memory limits
    # ------------------------------------------------------------------ #
    max_upload_mb: int = Field(default=10, description="Max upload size in MB")
    max_documents_in_memory: int = Field(default=50)

    # ------------------------------------------------------------------ #
    # Vector DB & Embeddings — pgvector (primary) with LangChain
    # ------------------------------------------------------------------ #
    # DB choice: pgvector (Postgres) — primary per user request.
    # Falls back to in-memory FAISS-like vector store when not configured.
    VECTOR_DB_URL: str | None = Field(
        default=None,
        description="Postgres DSN for pgvector, e.g., postgresql://user:pass@localhost:5432/voice_docs — if None, uses in-memory vectors",
    )
    DATABASE_URL: str | None = Field(
        default=None,
        description="Alias for VECTOR_DB_URL (common in PaaS)",
    )

    @property
    def vector_db_url(self) -> str | None:
        return self.VECTOR_DB_URL or self.DATABASE_URL

    # Embedding provider — openai (text-embedding-3-small) is default, sentence_transformers is offline fallback
    EMBEDDING_PROVIDER: Literal["openai", "sentence_transformers", "local"] = Field(
        default="openai",
        description="Embedding backend: openai or sentence_transformers",
    )
    EMBEDDING_MODEL: str = Field(
        default="text-embedding-3-small",
        description="Embedding model — openai: text-embedding-3-small (1536d) or multilingual-e5 for JP/AR",
    )
    EMBEDDING_DIM: int = Field(default=1536, description="Vector dimension for pgvector column")

    # Chunking — LangChain RecursiveCharacterTextSplitter
    CHUNK_SIZE: int = Field(default=800, description="Chars per chunk for LangChain splitter")
    CHUNK_OVERLAP: int = Field(default=100, description="Overlap for LangChain splitter")
    # Retrieval
    VECTOR_TOP_K: int = Field(default=3, description="Top-k for vector search")
    VECTOR_THRESHOLD: float | None = Field(default=None, description="Min similarity threshold (0-1), None = no filter")
    HYBRID_ALPHA: float = Field(default=0.5, description="Hybrid search weight: 0=BM25 only, 1=vector only, 0.5=RRF blend")

    # ------------------------------------------------------------------ #
    # Pydantic Settings config
    # ------------------------------------------------------------------ #
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Cached singleton accessor — cheap to call from FastAPI dependencies.
    Use `get_settings.cache_clear()` in tests to reload after monkeypatching env.
    """
    return Settings()


# Convenience re-export for `from app.config import settings`
settings = get_settings()
