"""
app/core/database.py — Dynamic Environment-Aware Persistence Layer

Strictly follows spec:
  - SQLAlchemy 2.0+ async ORM (DeclarativeBase, Mapped, mapped_column)
  - Dynamic switch: if DATABASE_URL exists and starts with "postgresql" -> asyncpg (PostgreSQL)
                   else -> aiosqlite SQLite file (./dev_voice_assistant.db)
  - User entity with id, username, email, hashed_password, preferred_ai_gender,
    custom_ai_name, created_at
  - Built-in metadata creation (init_db) — no Alembic required for prototype,
    but compatible with `alembic revision --autogenerate`.

Clean Architecture: Infrastructure adapter — presentation/application depend on
get_db() dependency, not on concrete engine.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator

from sqlalchemy import String, DateTime, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import get_settings

# ---------------------------------------------------------------------------
# 1. Dynamic Database Switch — environment-aware engine factory
# ---------------------------------------------------------------------------

def _resolve_database_url() -> tuple[str, bool]:
    """
    Resolve the database URL per spec.

    Priority:
      1. DATABASE_URL env var (Render, Railway, Neon, Supabase)
      2. VECTOR_DB_URL / DATABASE_URL from Settings (Pydantic loads .env)
      3. Fallback to SQLite file for local/dev/mock

    Returns:
        (async_url, is_postgres)
        async_url is SQLAlchemy async URL (postgresql+asyncpg:// or sqlite+aiosqlite://)
        is_postgres True if production PostgreSQL, False if local SQLite
    """
    settings = get_settings()

    # Raw env var takes precedence — Render injects DATABASE_URL=postgresql://...
    raw_url = os.getenv("DATABASE_URL") or settings.DATABASE_URL or settings.VECTOR_DB_URL or ""

    raw_url = (raw_url or "").strip()

    # Remove surrounding quotes that some PaaS inject
    if raw_url.startswith("'") and raw_url.endswith("'"):
        raw_url = raw_url[1:-1]
    if raw_url.startswith('"') and raw_url.endswith('"'):
        raw_url = raw_url[1:-1]

    # Detect production PostgreSQL per spec
    # Spec: "If DATABASE_URL starts with postgresql, instantiate asyncpg"
    is_postgres = raw_url.startswith("postgresql://") or raw_url.startswith("postgres://")

    # Also treat Render's "production" env as signal even if URL is postgres-like
    if not is_postgres and os.getenv("ENVIRONMENT", "").lower() == "production":
        # If ENVIRONMENT=production but no DATABASE_URL, still warn — but fallback to SQLite
        pass

    if is_postgres:
        # Normalize: postgres:// -> postgresql+asyncpg:// for SQLAlchemy async
        # asyncpg is the spec-required driver for PostgreSQL
        if raw_url.startswith("postgres://"):
            raw_url = raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif raw_url.startswith("postgresql://"):
            raw_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        # Ensure asyncpg param is present — some URLs already have it
        async_url = raw_url
    else:
        # Development / mock fallback — localized SQLite file per spec
        # Spec: "sqlite+aiosqlite:///./dev_voice_assistant.db"
        # Use aiosqlite for async support
        async_url = "sqlite+aiosqlite:///./dev_voice_assistant.db"

    return async_url, is_postgres


# Resolve once at import — but re-evaluated on get_engine() for tests that monkeypatch env
DATABASE_URL, IS_POSTGRES = _resolve_database_url()

# Engine kwargs differ for SQLite vs Postgres
# - SQLite: check_same_thread=False, no pool size
# - Postgres (asyncpg): pool_pre_ping, pool_size
if IS_POSTGRES:
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,  # set True for SQL debugging
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )
else:
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        connect_args={"check_same_thread": False},
    )

# Async session factory — used as FastAPI dependency
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# ---------------------------------------------------------------------------
# 2. Declarative Base — SQLAlchemy 2.0 style
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass

# ---------------------------------------------------------------------------
# 3. Database Schema Models — User entity per spec
# ---------------------------------------------------------------------------

class User(Base):
    """
    User entity — enterprise auth + personalization per spec.

    Fields:
      id: UUID primary key (also supports Integer autoincrement for SQLite compat)
      username: unique, indexed
      email: unique, indexed (used for signin + SMTP summary)
      hashed_password: bcrypt hash, never plain text
      preferred_ai_gender: 'male' | 'female' — drives Aoede/Charon voice
      custom_ai_name: personalized assistant name (e.g., "Milo", "Ava")
      created_at: UTC timestamp
    """
    __tablename__ = "users"

    # Use String UUID for portability across SQLite/Postgres
    # Postgres could use UUID type, but String works for both without extra extension
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    preferred_ai_gender: Mapped[str] = mapped_column(String(10), nullable=False, default="female")
    custom_ai_name: Mapped[str] = mapped_column(String(100), nullable=False, default="Aria")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username} email={self.email} gender={self.preferred_ai_gender} ai_name={self.custom_ai_name}>"

# ---------------------------------------------------------------------------
# 4. Session dependency + init helper
# ---------------------------------------------------------------------------

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency — yields an async session and ensures close.

    Usage:
        @router.post("/api/auth/signup")
        async def signup(payload: SignupRequest, db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

async def init_db() -> None:
    """
    Create all tables if not exist — built-in metadata handler per spec.

    Called on app startup (lifespan). For production migrations, replace with:
        alembic upgrade head
    But for prototype, this ensures `users` table exists on both SQLite and Postgres
    without requiring `alembic revision`.

    Also handles dynamic switch: re-resolves DATABASE_URL on each call so tests
    that monkeypatch env get correct engine (re-creates engine if URL changed).
    """
    global engine, AsyncSessionLocal, DATABASE_URL, IS_POSTGRES

    # Re-resolve in case env changed since import (important for tests)
    new_url, new_is_pg = _resolve_database_url()
    if new_url != DATABASE_URL:
        # Re-create engine if URL changed (e.g., test switching to postgres)
        DATABASE_URL, IS_POSTGRES = new_url, new_is_pg
        if IS_POSTGRES:
            engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True, pool_size=10, max_overflow=20)
        else:
            engine = create_async_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})
        AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False, autocommit=False)

    async with engine.begin() as conn:
        # Run sync metadata create in async context
        await conn.run_sync(Base.metadata.create_all)

# Helper for direct queries outside FastAPI dependency (e.g., WebSocket hydration)
async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalars().first()

async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    result = await db.execute(select(User).where(User.username == username))
    return result.scalars().first()
