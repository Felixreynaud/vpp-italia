"""Unit tests for api/dependencies.py and api/main.py."""

from __future__ import annotations

import contextlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# close_db
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_db_when_no_engine_does_nothing() -> None:
    from api import dependencies

    saved = dependencies._engine
    dependencies._engine = None
    try:
        await dependencies.close_db()
    finally:
        dependencies._engine = saved


@pytest.mark.asyncio
async def test_close_db_disposes_engine() -> None:
    from api import dependencies

    mock_engine = AsyncMock()
    saved = dependencies._engine
    dependencies._engine = mock_engine
    try:
        await dependencies.close_db()
    finally:
        dependencies._engine = saved

    mock_engine.dispose.assert_awaited_once()


# ---------------------------------------------------------------------------
# get_db
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_db_yields_session_and_commits() -> None:
    from api import dependencies

    mock_session = AsyncMock()
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_cm)

    saved = dependencies._session_factory
    dependencies._session_factory = mock_factory
    try:
        gen = dependencies.get_db()
        session = await gen.__anext__()
        assert session is mock_session
        with contextlib.suppress(StopAsyncIteration):
            await gen.__anext__()
    finally:
        dependencies._session_factory = saved

    mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_db_rollbacks_on_exception() -> None:
    from api import dependencies

    mock_session = AsyncMock()
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_cm)

    saved = dependencies._session_factory
    dependencies._session_factory = mock_factory
    try:
        gen = dependencies.get_db()
        await gen.__anext__()
        with pytest.raises(RuntimeError):
            await gen.athrow(RuntimeError("db error"))
    finally:
        dependencies._session_factory = saved

    mock_session.rollback.assert_awaited_once()


# ---------------------------------------------------------------------------
# get_current_user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_current_user_valid_token() -> None:
    from jose import jwt

    from api.dependencies import get_current_user

    token = jwt.encode(
        {"sub": "user-42", "roles": ["admin"]}, "test-secret-key-ci", algorithm="HS256"
    )

    with patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret-key-ci"}):
        result = await get_current_user(token)

    assert result["user_id"] == "user-42"
    assert result["roles"] == ["admin"]


@pytest.mark.asyncio
async def test_get_current_user_invalid_token_raises_401() -> None:
    from api.dependencies import get_current_user

    with (
        patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret-key-ci"}),
        pytest.raises(HTTPException) as exc_info,
    ):
        await get_current_user("not.a.valid.token")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_missing_sub_raises_401() -> None:
    from jose import jwt

    from api.dependencies import get_current_user

    token = jwt.encode({"roles": ["admin"]}, "test-secret-key-ci", algorithm="HS256")

    with (
        patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret-key-ci"}),
        pytest.raises(HTTPException) as exc_info,
    ):
        await get_current_user(token)

    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# api/main.py — root endpoint and exception handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_root_returns_api_info(client) -> None:
    resp = await client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "VPP Italia API"
    assert "version" in body
    assert "docs" in body


@pytest.mark.asyncio
async def test_exception_handler_returns_500() -> None:
    from api.main import unhandled_exception_handler

    mock_request = MagicMock()
    mock_request.url.path = "/broken"
    mock_request.url.__str__ = lambda self: "http://test/broken"

    response = await unhandled_exception_handler(mock_request, RuntimeError("unexpected"))

    assert response.status_code == 500
    import json

    body = json.loads(response.body)
    assert body["status"] == 500
    assert body["title"] == "Internal Server Error"
