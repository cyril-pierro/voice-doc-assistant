"""
app/auth/routes.py — Enterprise Authentication (Sign Up / Sign In)

Spec:
  - POST /api/auth/signup — username, email, password, retype_password, preferred_ai_gender, custom_ai_name
    -> verify password==retype_password, check email/username not taken, hash with passlib+bcrypt, insert User
  - POST /api/auth/signin — email, password -> validate hash, return session token/cookie
  - Uses SQLAlchemy 2.0 async, dynamic SQLite/Postgres via app.core.database

Clean Architecture: Presentation layer — thin controllers, business rules in handler,
persistence via get_db() dependency. No plain-text passwords ever stored.
"""

from __future__ import annotations

import re
import secrets
import logging
from datetime import datetime, timezone, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import User, get_db

logger = logging.getLogger("voice-doc-assistant")

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# Password hashing — passlib + bcrypt per spec (with 72-byte fix)
# ---------------------------------------------------------------------------
# Bcrypt has a 72-byte hard limit. Passlib's bcrypt handler raises
# ValueError for longer passwords (e.g., "password cannot be longer than 72 bytes").
# We handle this by pre-hashing long passwords with SHA-256 + base64,
# so users can input any length (e.g., 200+ char passphrases) securely.
# This is the industry-standard workaround for bcrypt + long passwords.
import hashlib
import base64


def _normalize_password(password: str) -> str:
    """
    Normalize password for bcrypt's 72-byte limit.
    If password bytes > 72, hash with SHA-256 and base64-encode to 44 chars.
    This preserves entropy and avoids truncation collisions.
    """
    pw_bytes = password.encode("utf-8")
    if len(pw_bytes) > 72:
        # SHA-256 -> 32 bytes -> base64 44 chars -> safely under 72
        return base64.b64encode(hashlib.sha256(pw_bytes).digest()).decode("ascii")
    return password


# Try passlib+bcrypt first (spec), with fixes for bcrypt 4.1+ and 72-byte limit
try:
    # Fix for bcrypt 4.1+ with passlib: passlib expects bcrypt.__about__.__version__
    # but newer bcrypt (4.1+) removed __about__. Monkey-patch before importing passlib.
    try:
        import bcrypt as _bcrypt_mod  # type: ignore

        if not hasattr(_bcrypt_mod, "__about__"):
            class _About:
                __version__ = getattr(_bcrypt_mod, "__version__", "4.0.1")

            _bcrypt_mod.__about__ = _About()  # type: ignore
    except Exception:
        pass

    from passlib.context import CryptContext

    # Use bcrypt — explicitly per spec. passlib handles salt + rounds.
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    def hash_password(password: str) -> str:
        return pwd_context.hash(_normalize_password(password))

    def verify_password(plain: str, hashed: str) -> bool:
        return pwd_context.verify(_normalize_password(plain), hashed)

    # Verify that pwd_context actually works (triggers bcrypt backend load at import time)
    # This catches the __about__ error and 72-byte issues early, not at first request
    _ = pwd_context.hash("test")

except (ImportError, AttributeError, ValueError, Exception) as _e:
    # Fallback for any passlib/bcrypt failure (missing, version mismatch, 72-byte, __about__)
    logger.warning(f"passlib/bcrypt not available or failed ({_e}) — using hashlib fallback (not as secure, but supports any length)")

    def hash_password(password: str) -> str:
        # Use same normalization for consistency (though hashlib has no 72-byte limit)
        return hashlib.sha256(_normalize_password(password).encode()).hexdigest()

    def verify_password(plain: str, hashed: str) -> bool:
        return hashlib.sha256(_normalize_password(plain).encode()).hexdigest() == hashed

# ---------------------------------------------------------------------------
# In-memory session store — legacy opaque tokens (kept for migration)
# JWT is primary; this store is only used for old clients that still hold
# opaque tokens issued before JWT migration.
# ---------------------------------------------------------------------------
# token -> {user_id, email, username, expires_at}
_SESSION_STORE: dict[str, dict] = {}


def _create_legacy_session_token(user: User) -> str:
    """Legacy opaque token — kept only for migration fallback."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    _SESSION_STORE[token] = {
        "user_id": user.id,
        "email": user.email,
        "username": user.username,
        "preferred_ai_gender": user.preferred_ai_gender,
        "custom_ai_name": user.custom_ai_name,
        "expires_at": expires,
    }
    return token


def get_session_by_token(token: str) -> dict | None:
    data = _SESSION_STORE.get(token)
    if not data:
        return None
    if data["expires_at"] < datetime.now(timezone.utc):
        _SESSION_STORE.pop(token, None)
        return None
    return data


def _create_session_token(user: User) -> str:
    """
    Primary: Create JWT bearer token (stateless).
    Also mirrors into _SESSION_STORE for optional revocation lookups
    and backwards-compat with code that calls get_session_by_token.
    """
    try:
        from app.auth.jwt import create_access_token

        token = create_access_token(
            user_id=str(user.id),
            username=user.username,
            email=user.email,
            preferred_ai_gender=user.preferred_ai_gender,
            custom_ai_name=user.custom_ai_name,
        )
        # Also store JWT in legacy store for debug / optional revocation
        # Expiry mirrors JWT expiry
        try:
            from app.config import get_settings

            expire_minutes = get_settings().JWT_EXPIRE_MINUTES
            expires = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
            _SESSION_STORE[token] = {
                "user_id": user.id,
                "email": user.email,
                "username": user.username,
                "preferred_ai_gender": user.preferred_ai_gender,
                "custom_ai_name": user.custom_ai_name,
                "expires_at": expires,
                "token_type": "jwt",
            }
        except Exception:
            pass
        return token
    except Exception as exc:
        logger.warning(f"JWT creation failed ({exc}) — falling back to legacy opaque token")
        return _create_legacy_session_token(user)


# ---------------------------------------------------------------------------
# Pydantic Schemas — strict validation per spec
# ---------------------------------------------------------------------------

class SignupRequest(BaseModel):
    username: str
    email: EmailStr
    password: str
    retype_password: str
    preferred_ai_gender: Literal["male", "female"]
    custom_ai_name: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Username must be at least 2 characters")
        if len(v) > 50:
            raise ValueError("Username too long (max 50)")
        if not re.match(r"^[a-zA-Z0-9_\-]+$", v):
            raise ValueError("Username may only contain letters, numbers, _ and -")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters")
        # No upper limit — bcrypt's 72-byte limit is handled via SHA-256 pre-hash
        # in _normalize_password(), so users can use any length (e.g., 200+ char passphrases)
        return v

    @field_validator("custom_ai_name")
    @classmethod
    def validate_ai_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 1:
            raise ValueError("Custom AI name is required")
        if len(v) > 50:
            raise ValueError("Custom AI name too long (max 50)")
        return v

    @field_validator("preferred_ai_gender")
    @classmethod
    def validate_gender(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in ("male", "female"):
            raise ValueError("preferred_ai_gender must be 'male' or 'female'")
        return v


class SigninRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    preferred_ai_gender: str
    custom_ai_name: str
    created_at: datetime

    class Config:
        from_attributes = True


class AuthResponse(BaseModel):
    message: str
    user: UserResponse
    session_token: str  # legacy — same as access_token for backwards compat
    access_token: str | None = None  # JWT bearer token
    token_type: str = "bearer"
    expires_in: int | None = None  # seconds until expiry

    @property
    def _compat(self):
        return self.session_token


# ---------------------------------------------------------------------------
# Routes — per spec
# ---------------------------------------------------------------------------

@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest, response: Response, db: AsyncSession = Depends(get_db)):
    """
    POST /api/auth/signup
    Spec: accept username, email, password, retype_password, preferred_ai_gender, custom_ai_name
          verify password == retype_password, check email/username not taken
    """
    # 1. Verify password == retype_password per spec
    if payload.password != payload.retype_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Passwords do not match (password vs retype_password)")

    # 2. Check email not taken
    existing_email = await db.execute(select(User).where(User.email == payload.email.lower().strip()))
    if existing_email.scalars().first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    # 3. Check username not taken
    existing_user = await db.execute(select(User).where(User.username == payload.username.strip()))
    if existing_user.scalars().first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken")

    # 4. Hash password with passlib+bcrypt per spec — never plain text
    hashed = hash_password(payload.password)

    # 5. Create user
    user = User(
        username=payload.username.strip(),
        email=payload.email.lower().strip(),
        hashed_password=hashed,
        preferred_ai_gender=payload.preferred_ai_gender.lower(),
        custom_ai_name=payload.custom_ai_name.strip(),
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    try:
        await db.commit()
        await db.refresh(user)
    except Exception as exc:
        await db.rollback()
        # Handle race condition on unique constraint
        if "UNIQUE" in str(exc) or "unique" in str(exc).lower():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username or email already exists")
        logger.exception(f"Signup DB error: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error during signup")

    # 6. Create JWT bearer token + secure cookie per spec
    token = _create_session_token(user)
    from app.config import get_settings as _gs

    _expire = _gs().JWT_EXPIRE_MINUTES * 60
    # Secure cookie — httpOnly, sameSite, JWT expiry
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        secure=False,  # set True in production with HTTPS
        samesite="lax",
        max_age=_expire,
        path="/",
    )
    # Also set a readable cookie for frontend state (non-httponly)
    response.set_cookie(key="user_email", value=user.email, httponly=False, samesite="lax", max_age=_expire, path="/")
    response.set_cookie(key="username", value=user.username, httponly=False, samesite="lax", max_age=_expire, path="/")

    logger.info(f"New user signed up: {user.username} <{user.email}> gender={user.preferred_ai_gender} ai_name={user.custom_ai_name}")

    return AuthResponse(
        message="Account created successfully",
        user=UserResponse.model_validate(user),
        session_token=token,
        access_token=token,
        token_type="bearer",
        expires_in=_expire,
    )


@router.post("/signin", response_model=AuthResponse)
async def signin(payload: SigninRequest, response: Response, db: AsyncSession = Depends(get_db)):
    """
    POST /api/auth/signin
    Spec: accept email + password, validate against hash, return session token/cookie
    """
    # 1. Find user by email
    result = await db.execute(select(User).where(User.email == payload.email.lower().strip()))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    # 2. Verify hash with passlib+bcrypt per spec
    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    # 3. Create JWT bearer token + cookie
    token = _create_session_token(user)
    from app.config import get_settings as _gs2

    _expire2 = _gs2().JWT_EXPIRE_MINUTES * 60
    response.set_cookie(key="session_token", value=token, httponly=True, secure=False, samesite="lax", max_age=_expire2, path="/")
    response.set_cookie(key="user_email", value=user.email, httponly=False, samesite="lax", max_age=_expire2, path="/")
    response.set_cookie(key="username", value=user.username, httponly=False, samesite="lax", max_age=_expire2, path="/")

    logger.info(f"User signed in: {user.username} <{user.email}>")

    return AuthResponse(
        message="Signed in successfully",
        user=UserResponse.model_validate(user),
        session_token=token,
        access_token=token,
        token_type="bearer",
        expires_in=_expire2,
    )


@router.post("/signout")
async def signout(response: Response, db: AsyncSession = Depends(get_db)):
    """Clear session cookies — frontend should also clear local state."""
    response.delete_cookie(key="session_token", path="/")
    response.delete_cookie(key="user_email", path="/")
    response.delete_cookie(key="username", path="/")
    return {"message": "Signed out"}


# Helper for /me dependency — extracts token from request (header or cookie)
from fastapi import Request as _Request


@router.get("/me", response_model=UserResponse)
async def get_me(
    request: _Request,
    db: AsyncSession = Depends(get_db),
):
    """
    GET /api/auth/me — returns current user if JWT bearer token valid.
    Accepts Authorization: Bearer <token> or session_token cookie or ?token=.
    """
    token_payload = _extract_token_payload(request)
    email = token_payload.get("email")
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    result = await db.execute(select(User).where(User.email == email.lower().strip()))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return UserResponse.model_validate(user)


class UpdateMeRequest(BaseModel):
    username: str | None = None
    preferred_ai_gender: Literal["male", "female"] | None = None
    custom_ai_name: str | None = None

    @field_validator("username")
    @classmethod
    def validate_username_opt(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Username must be at least 2 characters")
        if len(v) > 50:
            raise ValueError("Username too long (max 50)")
        if not re.match(r"^[a-zA-Z0-9_\\-]+$", v):
            raise ValueError("Username may only contain letters, numbers, _ and -")
        return v

    @field_validator("custom_ai_name")
    @classmethod
    def validate_ai_name_opt(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if len(v) < 1:
            raise ValueError("Custom AI name is required")
        if len(v) > 50:
            raise ValueError("Custom AI name too long (max 50)")
        return v


@router.put("/me", response_model=AuthResponse)
async def update_me(
    payload: UpdateMeRequest,
    request: _Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    PUT /api/auth/me — update current user's configuration (JWT required).
    Allows updating username, preferred_ai_gender, custom_ai_name.
    Re-issues JWT with updated claims and updates cookies.
    """
    token_payload = _extract_token_payload(request)
    email = token_payload.get("email")
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    result = await db.execute(select(User).where(User.email == email.lower().strip()))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    updated = False
    if payload.username is not None and payload.username.strip() != user.username:
        new_u = payload.username.strip()
        # check uniqueness
        existing = await db.execute(select(User).where(User.username == new_u))
        if existing.scalars().first():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken")
        user.username = new_u
        updated = True
    if payload.preferred_ai_gender is not None and payload.preferred_ai_gender.lower() != user.preferred_ai_gender:
        if payload.preferred_ai_gender.lower() not in ("male", "female"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="preferred_ai_gender must be male/female")
        user.preferred_ai_gender = payload.preferred_ai_gender.lower()
        updated = True
    if payload.custom_ai_name is not None and payload.custom_ai_name.strip() != user.custom_ai_name:
        user.custom_ai_name = payload.custom_ai_name.strip()
        updated = True

    if updated:
        try:
            await db.commit()
            await db.refresh(user)
        except Exception as exc:
            await db.rollback()
            if "UNIQUE" in str(exc) or "unique" in str(exc).lower():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error")

    # Re-issue JWT with updated claims
    new_token = _create_session_token(user)
    from app.config import get_settings as _gs_upd

    _expire_upd = _gs_upd().JWT_EXPIRE_MINUTES * 60
    response.set_cookie(key="session_token", value=new_token, httponly=True, secure=False, samesite="lax", max_age=_expire_upd, path="/")
    response.set_cookie(key="user_email", value=user.email, httponly=False, samesite="lax", max_age=_expire_upd, path="/")
    response.set_cookie(key="username", value=user.username, httponly=False, samesite="lax", max_age=_expire_upd, path="/")

    logger.info(f"User updated config: {user.username} <{user.email}> gender={user.preferred_ai_gender} ai_name={user.custom_ai_name}")

    return AuthResponse(
        message="Configuration updated" if updated else "No changes",
        user=UserResponse.model_validate(user),
        session_token=new_token,
        access_token=new_token,
        token_type="bearer",
        expires_in=_expire_upd,
    )


def _extract_token_payload(request: _Request) -> dict:
    """
    Extract and verify JWT from Authorization: Bearer <token> or session_token cookie.
    Used by /me and can be reused elsewhere. Raises 401 on failure.
    """
    # Check header first
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    token: str | None = None
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = request.cookies.get("session_token")
    if not token:
        # also check query param ?token= for flexibility
        token = request.query_params.get("token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    # Try JWT then legacy
    try:
        from app.auth.jwt import get_user_claims_from_any_token

        claims = get_user_claims_from_any_token(token)
        if claims and claims.get("token_type") == "jwt":
            return claims.get("payload")  # type: ignore
        if claims:
            # legacy token — return its dict as payload-like
            return {"email": claims.get("email"), "username": claims.get("username"), "sub": claims.get("user_id")}
    except Exception:
        pass
    # Fallback direct JWT decode for detailed error
    try:
        from app.auth.jwt import decode_access_token

        return decode_access_token(token)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid or expired token: {exc}")


@router.post("/refresh", response_model=AuthResponse)
async def refresh_token(
    request: _Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    POST /api/auth/refresh — issue a new JWT if current token is still valid.
    Accepts Authorization: Bearer <token> or session_token cookie.
    """
    payload = _extract_token_payload(request)
    email = payload.get("email")
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    result = await db.execute(select(User).where(User.email == email.lower().strip()))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    new_token = _create_session_token(user)
    from app.config import get_settings as _gs3

    _expire3 = _gs3().JWT_EXPIRE_MINUTES * 60
    response.set_cookie(key="session_token", value=new_token, httponly=True, secure=False, samesite="lax", max_age=_expire3, path="/")
    return AuthResponse(
        message="Token refreshed",
        user=UserResponse.model_validate(user),
        session_token=new_token,
        access_token=new_token,
        token_type="bearer",
        expires_in=_expire3,
    )

