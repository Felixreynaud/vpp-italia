"""Authentication crypto helpers — bcrypt + JWT + refresh tokens.

Centralises every primitive so the route handlers stay free of crypto
boilerplate. Read configuration from environment:

    JWT_SECRET_KEY              (required)
    JWT_ALGORITHM               (default HS256)
    ACCESS_TOKEN_EXPIRES_MIN    (default 15)
    REFRESH_TOKEN_EXPIRES_DAYS  (default 7)
    LOGIN_MAX_FAILED_ATTEMPTS   (default 5)
    LOGIN_LOCKOUT_MINUTES       (default 15)
"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt

# bcrypt enforces a 72-byte ceiling on the secret. We truncate explicitly
# rather than relying on undocumented backend behavior. Passwords longer than
# 72 bytes effectively share the same hash from byte 72 onward — acceptable
# given our 128-char Pydantic cap.
_BCRYPT_MAX_BYTES = 72


# ---------------------------------------------------------------------------
# Configuration accessors
# ---------------------------------------------------------------------------


def _jwt_secret() -> str:
    return os.environ["JWT_SECRET_KEY"]


def _jwt_algorithm() -> str:
    return os.getenv("JWT_ALGORITHM", "HS256")


def access_token_ttl() -> timedelta:
    return timedelta(minutes=int(os.getenv("ACCESS_TOKEN_EXPIRES_MIN", "15")))


def refresh_token_ttl() -> timedelta:
    return timedelta(days=int(os.getenv("REFRESH_TOKEN_EXPIRES_DAYS", "7")))


def login_max_failed_attempts() -> int:
    return int(os.getenv("LOGIN_MAX_FAILED_ATTEMPTS", "5"))


def login_lockout_duration() -> timedelta:
    return timedelta(minutes=int(os.getenv("LOGIN_LOCKOUT_MINUTES", "15")))


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    secret = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(secret, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-time verification. Returns False if no hash is set."""
    if not password_hash:
        return False
    secret = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    try:
        return bcrypt.checkpw(secret, password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# JWT access tokens
# ---------------------------------------------------------------------------


def create_access_token(user_id: str, role: str, *, email: str | None = None) -> str:
    expires = datetime.now(UTC) + access_token_ttl()
    payload: dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "roles": [role],
        "exp": expires,
        "type": "access",
    }
    if email:
        payload["email"] = email
    token: str = jwt.encode(payload, _jwt_secret(), algorithm=_jwt_algorithm())
    return token


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode an access token. Raises jose.JWTError on any failure."""
    payload: dict[str, Any] = jwt.decode(token, _jwt_secret(), algorithms=[_jwt_algorithm()])
    if payload.get("type") not in (None, "access"):
        raise JWTError("Not an access token")
    return payload


# ---------------------------------------------------------------------------
# Refresh tokens (opaque random, hashed at rest)
# ---------------------------------------------------------------------------


def generate_refresh_token() -> str:
    """Return a cryptographically secure random token (43 chars, ~256 bits)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Stable hash for refresh / reset tokens. SHA-256 is sufficient since the
    plaintext is already high-entropy random — no need for bcrypt's slow KDF."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
