"""Seed the database with a default admin user (idempotent).

Reads credentials from environment variables:
    ADMIN_DEFAULT_EMAIL     (default: admin@vpp-italia.local)
    ADMIN_DEFAULT_PASSWORD  (required if no admin exists yet)
    ADMIN_DEFAULT_FULLNAME  (default: Default Admin)

The script does nothing if at least one active admin already exists,
so it is safe to run at every deployment.

Usage:
    python -m scripts.seed_admin
"""

from __future__ import annotations

import asyncio
import os
import sys

import structlog
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from data.models import Base, User, UserRole

logger = structlog.get_logger(__name__)

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def seed_admin() -> int:
    """Return 0 if an admin already exists or was created, non-zero on error."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("seed_admin.missing_database_url")
        return 2

    email = os.environ.get("ADMIN_DEFAULT_EMAIL", "admin@vpp-italia.local").strip().lower()
    password = os.environ.get("ADMIN_DEFAULT_PASSWORD")
    full_name = os.environ.get("ADMIN_DEFAULT_FULLNAME", "Default Admin")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Ensure tables exist (no-op if already created by api.dependencies.init_db)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        existing = await session.execute(
            select(User).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
        )
        if existing.scalar_one_or_none() is not None:
            logger.info("seed_admin.skipped_admin_exists")
            await engine.dispose()
            return 0

        if not password:
            logger.error(
                "seed_admin.missing_password",
                hint="Set ADMIN_DEFAULT_PASSWORD env var to bootstrap the first admin",
            )
            await engine.dispose()
            return 3

        user = User(
            email=email,
            password_hash=_pwd_context.hash(password),
            full_name=full_name,
            role=UserRole.ADMIN,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        logger.info("seed_admin.admin_created", email=email)

    await engine.dispose()
    return 0


def main() -> None:
    sys.exit(asyncio.run(seed_admin()))


if __name__ == "__main__":
    main()
