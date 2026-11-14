"""JWT creation/verification and per-user authorization for the dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import jwt
from fastapi import Request, Response
from fastapi.responses import JSONResponse

from config import config
from core import users
from core.logger import app_logger

# Paths reachable without a token
PUBLIC_PATHS: frozenset[str] = frozenset({"/", "/api/login", "/api/signup", "/favicon.ico"})


def create_token(user: dict[str, Any]) -> str:
    """Issue a JWT for an authenticated user."""
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(hours=config.jwt_expire_hours)
    payload: dict[str, Any] = {
        "sub": user["username"],
        "uid": user["id"],
        "adm": bool(user["is_admin"]),
        "iat": issued_at,
        "exp": expires_at,
    }
    return jwt.encode(payload, config.jwt_secret, algorithm=config.jwt_algorithm)


def verify_token(token: str) -> dict[str, Any] | None:
    """Decode a JWT; return the payload or ``None`` if invalid/expired."""
    try:
        return jwt.decode(token, config.jwt_secret, algorithms=[config.jwt_algorithm])
    except jwt.PyJWTError as exc:
        app_logger.warning("Token không hợp lệ: %s", exc)
        return None


def resolve_user(token: str) -> dict[str, Any] | None:
    """Validate a JWT *and* confirm the account still exists and is enabled.

    This makes "Disable" in the Users panel take effect immediately instead of
    waiting for the token to expire. Admin status comes from the database, not
    from the (client-held) token.
    """
    payload = verify_token(token)
    if payload is None or not isinstance(payload.get("uid"), int):
        return None
    return users.get_active_user(payload["uid"])


async def auth_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Reject requests without a valid JWT (except public paths)."""
    path = request.url.path
    if request.method == "OPTIONS" or path in PUBLIC_PATHS:
        return await call_next(request)

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    user = resolve_user(header.removeprefix("Bearer ").strip())
    if user is None:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    request.state.user = user
    return await call_next(request)


__all__ = ["PUBLIC_PATHS", "auth_middleware", "create_token", "resolve_user", "verify_token"]
