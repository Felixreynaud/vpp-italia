"""Authentication endpoints — production-grade.

Login flow:
    POST /api/v1/auth/login
        in:  { email, password }
        out: { access_token, token_type } + Set-Cookie refresh_token (httpOnly, 7d)

Session lifecycle:
    GET  /api/v1/auth/me        — current user profile
    POST /api/v1/auth/refresh   — new access token from refresh cookie
    POST /api/v1/auth/logout    — revoke refresh token, clear cookie
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, Cookie, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from api import security
from api.dependencies import CurrentUser, DbSession
from api.email import (
    EmailMessage,
    frontend_base_url,
    get_email_backend,
    render_password_reset_email,
)
from data.models import (
    PasswordResetPurpose,
    PasswordResetToken,
    RefreshToken,
    User,
    UserRole,
)
from data.schemas import (
    PasswordChangeRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
)

logger = structlog.get_logger(__name__)

router = APIRouter()

REFRESH_COOKIE_NAME = "refresh_token"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    # Accept both "email" (canonical) and legacy "username" for backwards compat.
    email: str | None = Field(default=None, max_length=255)
    username: str | None = Field(default=None, max_length=255)
    password: str = Field(..., max_length=128)

    def resolved_email(self) -> str:
        candidate = (self.email or self.username or "").strip().lower()
        return candidate


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict[str, Any]


class MeResponse(BaseModel):
    user_id: str
    email: str
    full_name: str
    role: UserRole
    is_active: bool


class GenericResponse(BaseModel):
    detail: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cookie_secure() -> bool:
    """HTTPS-only cookie in production, relaxed for local dev/tests."""
    return os.getenv("APP_ENV", "development").lower() in ("production", "staging")


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        max_age=int(security.refresh_token_ttl().total_seconds()),
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/api/v1/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/api/v1/auth")


async def _issue_refresh_token(
    session: DbSession,
    user: User,
    request: Request,
) -> str:
    plain = security.generate_refresh_token()
    token_hash = security.hash_token(plain)
    expires_at = datetime.now(UTC) + security.refresh_token_ttl()
    ua = (request.headers.get("user-agent") or "")[:255] or None
    ip = request.client.host if request.client else None

    session.add(
        RefreshToken(
            user_id=user.user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=ua,
            ip_address=ip,
        )
    )
    await session.commit()
    return plain


async def _revoke_refresh_token(session: DbSession, plain_token: str) -> None:
    token_hash = security.hash_token(plain_token)
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == token_hash, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await session.commit()


async def _load_active_refresh_token(
    session: DbSession, plain_token: str
) -> RefreshToken | None:
    token_hash = security.hash_token(plain_token)
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    rt = result.scalar_one_or_none()
    if rt is None:
        return None
    now = datetime.now(UTC)
    expires = rt.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if rt.revoked_at is not None or expires < now:
        return None
    return rt


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/api/v1/auth/login", response_model=LoginResponse, tags=["auth"])
async def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
) -> Any:
    email = req.resolved_email()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="email is required"
        )

    invalid_credentials = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        # Same response whether the user exists or not — defeats enumeration.
        raise invalid_credentials

    now = datetime.now(UTC)
    locked_until = user.locked_until
    if locked_until is not None and locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=UTC)

    if locked_until is not None and locked_until > now:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Account temporarily locked, retry later",
        )

    if not security.verify_password(req.password, user.password_hash):
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        if user.failed_login_attempts >= security.login_max_failed_attempts():
            user.locked_until = now + security.login_lockout_duration()
            logger.warning("auth.lockout", email=email, attempts=user.failed_login_attempts)
        await session.commit()
        raise invalid_credentials

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    await session.commit()

    access_token = security.create_access_token(
        user_id=str(user.user_id), role=str(user.role), email=user.email
    )
    refresh_plain = await _issue_refresh_token(session, user, request)
    _set_refresh_cookie(response, refresh_plain)

    logger.info("auth.login_success", user_id=str(user.user_id), email=email)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "user_id": str(user.user_id),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
        },
    }


@router.post("/api/v1/auth/refresh", response_model=LoginResponse, tags=["auth"])
async def refresh(
    request: Request,
    response: Response,
    session: DbSession,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> Any:
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
    )
    if not refresh_token:
        raise invalid

    rt = await _load_active_refresh_token(session, refresh_token)
    if rt is None:
        raise invalid

    user = await session.get(User, rt.user_id)
    if user is None or not user.is_active:
        raise invalid

    # Rotate: revoke the old token, issue a fresh one (defence-in-depth).
    await _revoke_refresh_token(session, refresh_token)
    new_refresh = await _issue_refresh_token(session, user, request)
    _set_refresh_cookie(response, new_refresh)

    access_token = security.create_access_token(
        user_id=str(user.user_id), role=str(user.role), email=user.email
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "user_id": str(user.user_id),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
        },
    }


@router.post("/api/v1/auth/logout", response_model=GenericResponse, tags=["auth"])
async def logout(
    response: Response,
    session: DbSession,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> Any:
    if refresh_token:
        await _revoke_refresh_token(session, refresh_token)
    _clear_refresh_cookie(response)
    return {"detail": "logged out"}


@router.get("/api/v1/auth/me", response_model=MeResponse, tags=["auth"])
async def me(current: CurrentUser, session: DbSession) -> Any:
    try:
        user_pk = uuid.UUID(str(current["user_id"]))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user identifier"
        ) from None
    user = await session.get(User, user_pk)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer active"
        )
    return {
        "user_id": str(user.user_id),
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "is_active": user.is_active,
    }


@router.post(
    "/api/v1/auth/change-password",
    response_model=GenericResponse,
    tags=["auth"],
)
async def change_password(
    payload: PasswordChangeRequest,
    current: CurrentUser,
    session: DbSession,
) -> Any:
    try:
        user_pk = uuid.UUID(str(current["user_id"]))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user identifier"
        ) from None

    user = await session.get(User, user_pk)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer active"
        )

    if not security.verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    if security.verify_password(payload.new_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must differ from the current one",
        )

    user.password_hash = security.hash_password(payload.new_password)
    await session.commit()

    logger.info("auth.password_changed", user_id=str(user.user_id))
    return {"detail": "password changed"}


# ---------------------------------------------------------------------------
# Password reset (forgot password flow)
# ---------------------------------------------------------------------------


_RESET_TOKEN_TTL = timedelta(hours=1)
_INVITE_TOKEN_TTL = timedelta(days=7)


async def _issue_password_reset_token(
    session: DbSession,
    user: User,
    purpose: PasswordResetPurpose,
) -> str:
    """Mint a one-shot token, persist its hash, return the plaintext."""
    plain = security.generate_refresh_token()
    token_hash = security.hash_token(plain)
    ttl = _INVITE_TOKEN_TTL if purpose == PasswordResetPurpose.INVITE else _RESET_TOKEN_TTL

    session.add(
        PasswordResetToken(
            user_id=user.user_id,
            token_hash=token_hash,
            purpose=purpose,
            expires_at=datetime.now(UTC) + ttl,
        )
    )
    await session.commit()
    return plain


async def _send_password_reset_email(
    user: User,
    plain_token: str,
    purpose: PasswordResetPurpose,
) -> None:
    reset_url = f"{frontend_base_url()}/reset-password?token={plain_token}"
    rendered = render_password_reset_email(
        full_name=user.full_name,
        reset_url=reset_url,
        is_invite=purpose == PasswordResetPurpose.INVITE,
    )
    backend = get_email_backend()
    await backend.send(
        EmailMessage(
            to=user.email,
            subject=rendered.subject,
            body_text=rendered.body_text,
            body_html=rendered.body_html,
        )
    )


@router.post(
    "/api/v1/auth/password-reset/request",
    response_model=GenericResponse,
    tags=["auth"],
)
async def request_password_reset(
    payload: PasswordResetRequest, session: DbSession
) -> Any:
    """Anti-enumeration: ALWAYS responds 200 with the same generic message.

    Whether the email exists or not, the response is identical and the timing
    is roughly similar (we still issue + send when it exists).
    """
    email = (payload.email or "").strip().lower()
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is not None and user.is_active:
        plain = await _issue_password_reset_token(
            session, user, PasswordResetPurpose.RESET
        )
        try:
            await _send_password_reset_email(user, plain, PasswordResetPurpose.RESET)
        except Exception:
            # Do not leak the failure — still return the generic 200.
            logger.exception("auth.reset_email_send_failed", user_id=str(user.user_id))
    else:
        logger.info("auth.reset_request_ignored", email=email)

    return {"detail": "If the email exists, a reset link has been sent."}


@router.post(
    "/api/v1/auth/password-reset/confirm",
    response_model=GenericResponse,
    tags=["auth"],
)
async def confirm_password_reset(
    payload: PasswordResetConfirm, session: DbSession
) -> Any:
    invalid = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid or expired token",
    )

    token_hash = security.hash_token(payload.token)
    result = await session.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    )
    token = result.scalar_one_or_none()
    if token is None:
        raise invalid

    now = datetime.now(UTC)
    expires = token.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if token.used_at is not None or expires < now:
        raise invalid

    user = await session.get(User, token.user_id)
    if user is None:
        raise invalid

    user.password_hash = security.hash_password(payload.new_password)
    # Invitation flow doubles as account activation.
    if not user.is_active:
        user.is_active = True
    user.failed_login_attempts = 0
    user.locked_until = None
    if user.email_verified_at is None:
        user.email_verified_at = now

    token.used_at = now
    await session.commit()

    logger.info(
        "auth.password_reset_confirmed",
        user_id=str(user.user_id),
        purpose=str(token.purpose),
    )
    return {"detail": "password set"}
