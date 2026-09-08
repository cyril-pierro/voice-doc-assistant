"""
app/auth/jwt.py — JWT creation & verification (HS256)

Stateless bearer token for HTTP routes + WebSocket.
Payload per user:
  sub: user.id
  username, email, preferred_ai_gender, custom_ai_name
  iat, exp, iss

Uses PyJWT (jwt) which is already installed (2.13.0).
Falls back to joserfc if PyJWT missing, else raises at import.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from app.config import get_settings

logger = logging.getLogger("voice-doc-assistant")

try:
    import jwt as _pyjwt  # PyJWT
    HAS_PYJWT = True
except ImportError:
    HAS_PYJWT = False
    _pyjwt = None  # type: ignore


def _get_secret_and_algo() -> tuple[str, str]:
    s = get_settings()
    return s.JWT_SECRET_KEY, s.JWT_ALGORITHM


def _get_expire_minutes() -> int:
    return get_settings().JWT_EXPIRE_MINUTES


def create_access_token(
    *,
    user_id: str,
    username: str,
    email: str,
    preferred_ai_gender: str | None = None,
    custom_ai_name: str | None = None,
    expires_minutes: int | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """
    Create a signed JWT bearer token for the given user.
    Expires in JWT_EXPIRE_MINUTES (default 7 days) unless overridden.
    """
    if not HAS_PYJWT:
        raise RuntimeError("PyJWT not installed — pip install PyJWT")

    secret, algo = _get_secret_and_algo()
    expire_minutes = expires_minutes if expires_minutes is not None else _get_expire_minutes()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=expire_minutes)

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "username": username,
        "email": email,
        "preferred_ai_gender": preferred_ai_gender or "female",
        "custom_ai_name": custom_ai_name or "Aria",
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "iss": "voice-doc-assistant",
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)

    token = _pyjwt.encode(payload, secret, algorithm=algo)
    # PyJWT 2.x returns str
    return token


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Decode and verify a JWT. Raises jwt exceptions on failure.
    Returns payload dict if valid.
    """
    if not HAS_PYJWT:
        raise RuntimeError("PyJWT not installed")

    secret, algo = _get_secret_and_algo()
    # Verify exp, iat, iss
    payload = _pyjwt.decode(
        token,
        secret,
        algorithms=[algo],
        issuer="voice-doc-assistant",
        options={"require": ["sub", "exp", "iat", "iss"]},
    )
    if payload.get("type") not in (None, "access"):
        # allow missing type for backwards compat, but reject refresh used as access
        if payload.get("type") == "refresh":
            raise _pyjwt.InvalidTokenError("refresh token cannot be used as access token")
    return payload


def verify_token(token: str) -> dict[str, Any] | None:
    """Safe wrapper — returns payload or None on any error (logs at debug)."""
    try:
        return decode_access_token(token)
    except Exception as exc:
        logger.debug(f"JWT verify failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Legacy compatibility — keep _SESSION_STORE fallback for old opaque tokens
# This allows old clients with opaque tokens to still work until they re-login.
# ---------------------------------------------------------------------------
def get_user_claims_from_any_token(token: str) -> dict[str, Any] | None:
    """
    Try JWT first, then legacy opaque token (secrets.token_urlsafe) from _SESSION_STORE.
    Returns a normalized dict with user_id, username, email, etc., or None.
    Used by dependencies to support both token types during migration.
    """
    # 1. Try JWT
    payload = verify_token(token)
    if payload is not None:
        return {
            "user_id": payload.get("sub"),
            "username": payload.get("username"),
            "email": payload.get("email"),
            "preferred_ai_gender": payload.get("preferred_ai_gender"),
            "custom_ai_name": payload.get("custom_ai_name"),
            "expires_at": datetime.fromtimestamp(payload["exp"], tz=timezone.utc) if "exp" in payload else None,
            "token_type": "jwt",
            "payload": payload,
        }
    # 2. Legacy fallback — opaque token stored in memory
    try:
        from app.auth.routes import get_session_by_token

        sess = get_session_by_token(token)
        if sess:
            return {
                "user_id": sess.get("user_id"),
                "username": sess.get("username"),
                "email": sess.get("email"),
                "preferred_ai_gender": sess.get("preferred_ai_gender"),
                "custom_ai_name": sess.get("custom_ai_name"),
                "expires_at": sess.get("expires_at"),
                "token_type": "legacy",
                "payload": sess,
            }
    except Exception:
        pass
    return None
