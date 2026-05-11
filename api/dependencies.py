"""Shared FastAPI dependencies: DB session, authentication."""

from collections.abc import AsyncGenerator
from typing import Annotated, Any

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from data.models import Base

logger = structlog.get_logger(__name__)

# Populated at startup via init_db()
_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


async def init_db() -> None:
    global _engine, _session_factory
    import os

    database_url = os.environ["DATABASE_URL"]
    _engine = create_async_engine(
        database_url,
        pool_size=int(os.getenv("DATABASE_POOL_SIZE", "20")),
        max_overflow=int(os.getenv("DATABASE_MAX_OVERFLOW", "40")),
        echo=os.getenv("DATABASE_ECHO", "false").lower() == "true",
    )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("db.initialized")


async def close_db() -> None:
    if _engine:
        await _engine.dispose()
        logger.info("db.closed")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    assert _session_factory is not None, "Database not initialized"
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> dict[str, Any]:
    import os

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            os.environ["JWT_SECRET_KEY"],
            algorithms=[os.getenv("JWT_ALGORITHM", "HS256")],
        )
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        return {"user_id": user_id, "roles": payload.get("roles", [])}
    except JWTError:
        raise credentials_exception from None


CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]
