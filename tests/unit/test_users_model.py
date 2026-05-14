"""Unit tests for User and PasswordResetToken ORM models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from data.models import PasswordResetPurpose, PasswordResetToken, User, UserRole


@pytest.mark.asyncio
async def test_user_defaults(db_session: AsyncSession) -> None:
    user = User(email="alice@example.com", full_name="Alice")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    assert user.user_id is not None
    assert user.role == UserRole.OPERATOR
    assert user.is_active is False
    assert user.password_hash is None
    assert user.failed_login_attempts == 0
    assert user.locked_until is None
    assert user.email_verified_at is None
    assert user.last_login_at is None


@pytest.mark.asyncio
async def test_user_email_unique(db_session: AsyncSession) -> None:
    db_session.add(User(email="dup@example.com", full_name="First"))
    await db_session.commit()

    db_session.add(User(email="dup@example.com", full_name="Second"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
async def test_user_role_admin(db_session: AsyncSession) -> None:
    user = User(
        email="admin@example.com",
        full_name="Boss",
        role=UserRole.ADMIN,
        is_active=True,
        password_hash="$2b$12$fake",
    )
    db_session.add(user)
    await db_session.commit()

    result = await db_session.execute(select(User).where(User.role == UserRole.ADMIN))
    fetched = result.scalar_one()
    assert fetched.email == "admin@example.com"
    assert fetched.role == UserRole.ADMIN


@pytest.mark.asyncio
async def test_password_reset_token_creation(db_session: AsyncSession) -> None:
    user = User(email="bob@example.com", full_name="Bob")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    token = PasswordResetToken(
        user_id=user.user_id,
        token_hash="a" * 64,
        purpose=PasswordResetPurpose.INVITE,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db_session.add(token)
    await db_session.commit()
    await db_session.refresh(token)

    assert token.token_id is not None
    assert token.used_at is None
    assert token.purpose == PasswordResetPurpose.INVITE


@pytest.mark.asyncio
async def test_password_reset_token_hash_unique(db_session: AsyncSession) -> None:
    user = User(email="carol@example.com", full_name="Carol")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    expiry = datetime.now(UTC) + timedelta(hours=1)
    db_session.add(
        PasswordResetToken(user_id=user.user_id, token_hash="dup-hash", expires_at=expiry)
    )
    await db_session.commit()

    db_session.add(
        PasswordResetToken(user_id=user.user_id, token_hash="dup-hash", expires_at=expiry)
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
async def test_password_reset_token_cascade_delete(db_session: AsyncSession) -> None:
    user = User(email="dave@example.com", full_name="Dave")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    user_id = user.user_id

    db_session.add(
        PasswordResetToken(
            user_id=user_id,
            token_hash="x" * 64,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await db_session.commit()

    await db_session.delete(user)
    await db_session.commit()

    remaining = await db_session.execute(
        select(PasswordResetToken).where(PasswordResetToken.user_id == user_id)
    )
    assert remaining.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_user_email_lookup_indexed(db_session: AsyncSession) -> None:
    """Sanity check: the email column is queryable by direct lookup."""
    for i in range(3):
        db_session.add(User(email=f"user{i}@example.com", full_name=f"User {i}"))
    await db_session.commit()

    result = await db_session.execute(select(User).where(User.email == "user1@example.com"))
    user = result.scalar_one()
    assert user.full_name == "User 1"
    assert user.user_id != uuid.UUID(int=0)
