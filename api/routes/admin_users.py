"""Admin endpoints for user management — admin role required.

Routes:
    GET    /api/v1/admin/users               list users
    POST   /api/v1/admin/users/invite        invite a new user
    PATCH  /api/v1/admin/users/{id}          update full_name / role / is_active
    DELETE /api/v1/admin/users/{id}          remove a user
    POST   /api/v1/admin/users/{id}/resend-invite
                                              re-issue an invitation token

Guard-rails enforced server-side:
- An admin cannot deactivate, demote, or delete themselves
- The last active admin cannot be deactivated, demoted, or deleted
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select

from api import security
from api.dependencies import DbSession, require_admin
from api.email import (
    EmailMessage,
    frontend_base_url,
    get_email_backend,
    render_password_reset_email,
)
from data.models import PasswordResetPurpose, PasswordResetToken, User, UserRole
from data.schemas import UserInvite, UserListResponse, UserResponse, UserUpdate

logger = structlog.get_logger(__name__)

router = APIRouter()

_INVITE_TTL = timedelta(days=7)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _count_active_admins(session: DbSession, *, excluding: uuid.UUID | None = None) -> int:
    stmt = (
        select(func.count())
        .select_from(User)
        .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
    )
    if excluding is not None:
        stmt = stmt.where(User.user_id != excluding)
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


def _parse_user_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user id"
        ) from None


# ---------------------------------------------------------------------------
# GET /api/v1/admin/users
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/admin/users",
    response_model=UserListResponse,
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)
async def list_users(session: DbSession) -> Any:
    result = await session.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()
    return {
        "data": [UserResponse.model_validate(u, from_attributes=True) for u in users],
        "meta": {"count": len(users)},
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/users/invite
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/admin/users/invite",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)
async def invite_user(payload: UserInvite, session: DbSession) -> Any:
    email = payload.email
    existing = await session.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    user = User(
        email=email,
        password_hash=None,
        full_name=payload.full_name,
        role=payload.role,
        is_active=False,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    plain = security.generate_refresh_token()
    session.add(
        PasswordResetToken(
            user_id=user.user_id,
            token_hash=security.hash_token(plain),
            purpose=PasswordResetPurpose.INVITE,
            expires_at=datetime.now(UTC) + _INVITE_TTL,
        )
    )
    await session.commit()

    reset_url = f"{frontend_base_url()}/reset-password?token={plain}"
    rendered = render_password_reset_email(
        full_name=user.full_name, reset_url=reset_url, is_invite=True
    )
    try:
        await get_email_backend().send(
            EmailMessage(
                to=user.email,
                subject=rendered.subject,
                body_text=rendered.body_text,
                body_html=rendered.body_html,
            )
        )
    except Exception:
        logger.exception("admin.invite_email_failed", user_id=str(user.user_id))
        # The user row is kept; admin can re-send via /resend-invite.

    logger.info("admin.user_invited", user_id=str(user.user_id), email=email)
    return UserResponse.model_validate(user, from_attributes=True)


# ---------------------------------------------------------------------------
# POST /api/v1/admin/users/{id}/resend-invite
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/admin/users/{user_id}/resend-invite",
    response_model=UserResponse,
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)
async def resend_invite(user_id: str, session: DbSession) -> Any:
    pk = _parse_user_id(user_id)
    user = await session.get(User, pk)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.is_active and user.password_hash is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has already activated their account",
        )

    plain = security.generate_refresh_token()
    session.add(
        PasswordResetToken(
            user_id=user.user_id,
            token_hash=security.hash_token(plain),
            purpose=PasswordResetPurpose.INVITE,
            expires_at=datetime.now(UTC) + _INVITE_TTL,
        )
    )
    await session.commit()

    reset_url = f"{frontend_base_url()}/reset-password?token={plain}"
    rendered = render_password_reset_email(
        full_name=user.full_name, reset_url=reset_url, is_invite=True
    )
    try:
        await get_email_backend().send(
            EmailMessage(
                to=user.email,
                subject=rendered.subject,
                body_text=rendered.body_text,
                body_html=rendered.body_html,
            )
        )
    except Exception:
        logger.exception("admin.invite_resend_email_failed", user_id=str(user.user_id))

    return UserResponse.model_validate(user, from_attributes=True)


# ---------------------------------------------------------------------------
# PATCH /api/v1/admin/users/{id}
# ---------------------------------------------------------------------------


@router.patch(
    "/api/v1/admin/users/{user_id}",
    response_model=UserResponse,
    tags=["admin"],
)
async def update_user(
    user_id: str,
    payload: UserUpdate,
    session: DbSession,
    admin: dict[str, Any] = Depends(require_admin),
) -> Any:
    pk = _parse_user_id(user_id)
    user = await session.get(User, pk)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    self_admin = str(admin.get("user_id")) == str(user.user_id)

    if payload.role is not None and payload.role != user.role:
        if self_admin and user.role == UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot demote yourself",
            )
        if user.role == UserRole.ADMIN and payload.role != UserRole.ADMIN:
            others = await _count_active_admins(session, excluding=user.user_id)
            if others == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="At least one active admin is required",
                )
        user.role = payload.role

    if payload.is_active is not None and payload.is_active != user.is_active:
        if self_admin and payload.is_active is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot deactivate yourself",
            )
        if (
            user.role == UserRole.ADMIN
            and user.is_active
            and payload.is_active is False
        ):
            others = await _count_active_admins(session, excluding=user.user_id)
            if others == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="At least one active admin is required",
                )
        user.is_active = payload.is_active

    if payload.full_name is not None:
        user.full_name = payload.full_name

    await session.commit()
    await session.refresh(user)
    logger.info("admin.user_updated", user_id=str(user.user_id))
    return UserResponse.model_validate(user, from_attributes=True)


# ---------------------------------------------------------------------------
# DELETE /api/v1/admin/users/{id}
# ---------------------------------------------------------------------------


@router.delete(
    "/api/v1/admin/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    tags=["admin"],
)
async def delete_user(
    user_id: str,
    session: DbSession,
    admin: dict[str, Any] = Depends(require_admin),
) -> None:
    pk = _parse_user_id(user_id)
    user = await session.get(User, pk)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if str(admin.get("user_id")) == str(user.user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete yourself",
        )

    if user.role == UserRole.ADMIN and user.is_active:
        others = await _count_active_admins(session, excluding=user.user_id)
        if others == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one active admin is required",
            )

    await session.delete(user)
    await session.commit()
    logger.info("admin.user_deleted", user_id=str(user.user_id))
