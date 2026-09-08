"""
app/presentation/dependencies/auth.py — Authentication dependency (JWT)

Presentation-layer concern: extracts SessionUser from HTTP/WS requests.
Business logic lives in application/domain; this is just the adapter.

Now validates JWT bearer tokens:
  - Authorization: Bearer <jwt>  (primary)
  - Cookie: session_token=<jwt>
  - Query param: ?token=<jwt>  (for WebSocket, since JS WebSocket can't set headers)
  - Legacy fallbacks: X-Username / username query param kept for guest/dev mode
    but any Bearer token present must be valid, otherwise 401.
"""

from __future__ import annotations

import logging

from fastapi import Cookie, Header, HTTPException, Query, Request, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger("voice-doc-assistant")
settings = get_settings()

# OpenAPI security scheme — makes Swagger UI show "Authorize" button
bearer_scheme = HTTPBearer(auto_error=False)


class SessionUser(BaseModel):
    username: str
    email: str
    user_id: str | None = None
    preferred_ai_gender: str | None = None
    custom_ai_name: str | None = None
    token_type: str | None = None  # "jwt" | "legacy" | "guest"
    # Extra for passing to providers
    language: str | None = None
    preferred_language: str | None = None


def _extract_token_from_headers(
    authorization: str | None,
    cookie_token: str | None,
    query_token: str | None,
) -> str | None:
    """Extract raw token from common locations (priority: header > cookie > query)."""
    if authorization and authorization.strip():
        # Handle "Bearer <token>" case-insensitively
        auth = authorization.strip()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        # Some clients send raw token without Bearer prefix
        # We only treat it as Bearer if it looks like JWT (contains dots)
        if auth.count(".") == 2:
            return auth
    if cookie_token:
        return cookie_token
    if query_token:
        return query_token
    return None


def _verify_and_build_user(token: str) -> SessionUser | None:
    """
    Verify JWT or legacy token and return SessionUser.
    Returns None if token invalid (caller should raise 401).
    """
    try:
        from app.auth.jwt import get_user_claims_from_any_token

        claims = get_user_claims_from_any_token(token)
        if claims:
            username = claims.get("username")
            email = claims.get("email")
            if username and email:
                return SessionUser(
                    username=username,
                    email=email,
                    user_id=claims.get("user_id"),
                    preferred_ai_gender=claims.get("preferred_ai_gender"),
                    custom_ai_name=claims.get("custom_ai_name"),
                    token_type=claims.get("token_type"),
                )
    except Exception as exc:
        logger.debug(f"Token verification failed: {exc}")
    return None


def get_session_user(
    request: Request,
    username: str | None = Query(default=None, description="Username via query param (legacy guest)"),
    email: str | None = Query(default=None, description="Email via query param (legacy guest)"),
    x_username: str | None = Header(default=None, alias="X-Username"),
    x_email: str | None = Header(default=None, alias="X-Email"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    cookie_token: str | None = Cookie(default=None, alias="session_token"),
    token: str | None = Query(default=None, description="JWT bearer token via query param (for WebSocket/legacy)"),
) -> SessionUser:
    """
    HTTP auth dependency — JWT-aware with guest fallback.

    Priority:
      1. Authorization: Bearer <jwt>  (validated, 401 if invalid)
      2. Cookie session_token=<jwt>   (validated, 401 if invalid)
      3. Query ?token=<jwt>           (validated, 401 if invalid)
      4. X-Headers / query username+email (legacy guest)
      5. Guest fallback if AUTH_REQUIRE_EMAIL=false
    If a bearer token is present but invalid/expired, raises 401 (does NOT fallback to guest).
    """
    # Extract token from any bearer location
    raw_token = _extract_token_from_headers(authorization, cookie_token, token)

    if raw_token:
        user = _verify_and_build_user(raw_token)
        if user:
            logger.debug(f"Authenticated via JWT: {user.username} <{user.email}> type={user.token_type}")
            return user
        # Token was present but verification failed -> must reject
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # No bearer token — fallback to legacy X-Headers / query params
    # This supports old clients and guest/dev mode
    resolved_username = x_username or username
    resolved_email = x_email or email

    # Backwards compat: Authorization: Bearer user:email (old fake bearer)
    if not resolved_username and authorization and authorization.startswith("Bearer "):
        fake = authorization.removeprefix("Bearer ").strip()
        # Only treat as fake bearer if not a JWT (JWT has 2 dots)
        if fake.count(".") != 2:
            if ":" in fake:
                u, e = fake.split(":", 1)
                resolved_username = u.strip()
                resolved_email = e.strip()
            else:
                resolved_username = fake

    if not resolved_username:
        if get_settings().auth_require_email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authentication. Provide Authorization: Bearer <token> or ?username & ?email",
                headers={"WWW-Authenticate": "Bearer"},
            )
        resolved_username = "guest"
        resolved_email = resolved_email or "guest@example.com"

    if not resolved_email:
        if get_settings().auth_require_email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing email. Provide ?email or X-Email header or Bearer token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        resolved_email = f"{resolved_username}@example.com"

    return SessionUser(username=resolved_username, email=resolved_email, token_type="guest")


def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    cookie_token: str | None = Cookie(default=None, alias="session_token"),
    token: str | None = Query(default=None, description="JWT bearer token via query param"),
) -> SessionUser:
    """
    Strict auth dependency — requires valid JWT bearer token, no guest fallback.
    Use this for protected routes that must be authenticated.

    Accepts Authorization: Bearer <token>, cookie session_token, or ?token query.
    Raises 401 if missing or invalid.
    """
    raw_token = _extract_token_from_headers(authorization, cookie_token, token)
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token. Provide Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = _verify_and_build_user(raw_token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# Alias for backwards compat — some code imports get_current_user_optional
def get_current_user_optional(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    cookie_token: str | None = Cookie(default=None, alias="session_token"),
    token: str | None = Query(default=None),
    username: str | None = Query(default=None),
    email: str | None = Query(default=None),
) -> SessionUser:
    """
    Optional auth — tries JWT first, falls back to guest (same as get_session_user).
    Kept for backwards compatibility.
    """
    return get_session_user(
        request=request,
        username=username,
        email=email,
        authorization=authorization,
        cookie_token=cookie_token,
        token=token,
    )


def resolve_user_from_ws(
    websocket: WebSocket,
    username: str | None = None,
    email: str | None = None,
    token: str | None = None,
) -> SessionUser | None:
    """
    WS variant — validates JWT if provided via query ?token=, Authorization header, or cookie.
    Returns None if auth required and missing (caller should close with 1008).
    Otherwise returns a SessionUser (guest fallback if allowed).

    Priority:
      1. ?token= query param (JS WebSocket can't set Authorization header, so this is primary for WS)
      2. Authorization header (if proxy forwards it)
      3. Cookie session_token
      4. Legacy username/email query params / headers
    If a token is present but invalid, returns None (caller will close).
    """
    # 1. Extract token: explicit token param > query ?token > header > cookie
    raw_token: str | None = None

    # WebSocket query params are in websocket.query_params
    query_token = websocket.query_params.get("token") or token
    auth_header = websocket.headers.get("authorization") or websocket.headers.get("Authorization")
    cookie_token = websocket.cookies.get("session_token")

    raw_token = _extract_token_from_headers(auth_header, cookie_token, query_token)

    if raw_token:
        user = _verify_and_build_user(raw_token)
        if user:
            logger.info(f"WS authenticated via JWT: {user.username} <{user.email}>")
            return user
        # Token present but invalid — reject
        logger.warning(f"WS rejected: invalid bearer token")
        return None

    # No bearer token — fallback to legacy username/email
    x_username = websocket.headers.get("x-username")
    x_email = websocket.headers.get("x-email")
    auth_header_legacy = auth_header

    resolved_username = x_username or username
    resolved_email = x_email or email

    # Legacy fake bearer: Authorization: Bearer user:email (old)
    if not resolved_username and auth_header_legacy and auth_header_legacy.startswith("Bearer "):
        fake = auth_header_legacy.removeprefix("Bearer ").strip()
        if fake.count(".") != 2 and ":" in fake:
            u, e = fake.split(":", 1)
            resolved_username = u.strip()
            resolved_email = e.strip()
        elif fake.count(".") != 2:
            resolved_username = fake

    if not resolved_username:
        if get_settings().auth_require_email:
            return None
        resolved_username = "guest"
        resolved_email = resolved_email or "guest@example.com"

    if not resolved_email:
        resolved_email = f"{resolved_username}@example.com"

    return SessionUser(username=resolved_username, email=resolved_email, token_type="guest")
