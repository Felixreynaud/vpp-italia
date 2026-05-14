"""Integration tests for the auth endpoints (login/refresh/logout/me)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api import security
from api.dependencies import get_current_user, get_db
from api.main import app
from data.models import RefreshToken, User, UserRole

_JWT_SECRET = "dev-secret-change-in-prod-openssl-rand-hex-32"


@pytest.fixture(autouse=True)
def _force_jwt_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", _JWT_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("LOGIN_MAX_FAILED_ATTEMPTS", "5")
    monkeypatch.setenv("LOGIN_LOCKOUT_MINUTES", "15")


@pytest_asyncio.fixture
async def auth_client(db_session: AsyncSession):
    """Client that does NOT auto-inject a fake current_user (we test the real flow)."""

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        email="admin@example.com",
        password_hash=security.hash_password("Str0ngP@ssword"),
        full_name="Admin User",
        role=UserRole.ADMIN,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def inactive_user(db_session: AsyncSession) -> User:
    user = User(
        email="inactive@example.com",
        password_hash=security.hash_password("Str0ngP@ssword"),
        full_name="Inactive",
        role=UserRole.OPERATOR,
        is_active=False,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


# ---------------------------------------------------------------------------
# /auth/login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_success_returns_access_token_and_refresh_cookie(
    auth_client: AsyncClient, admin_user: User
) -> None:
    resp = await auth_client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "Str0ngP@ssword"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == "admin@example.com"
    assert body["user"]["role"] == "admin"

    # Refresh cookie present, httpOnly
    set_cookie = resp.headers.get("set-cookie", "")
    assert "refresh_token=" in set_cookie
    assert "HttpOnly" in set_cookie

    payload = jwt.decode(body["access_token"], _JWT_SECRET, algorithms=["HS256"])
    assert payload["sub"] == str(admin_user.user_id)
    assert payload["role"] == "admin"


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(
    auth_client: AsyncClient, admin_user: User
) -> None:
    resp = await auth_client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "wrong"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


@pytest.mark.asyncio
async def test_login_unknown_email_returns_same_401(auth_client: AsyncClient) -> None:
    """Anti-enumeration: same response as wrong password."""
    resp = await auth_client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "whatever"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


@pytest.mark.asyncio
async def test_login_inactive_user_returns_401(
    auth_client: AsyncClient, inactive_user: User
) -> None:
    resp = await auth_client.post(
        "/api/v1/auth/login",
        json={"email": "inactive@example.com", "password": "Str0ngP@ssword"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_lockout_after_5_failed_attempts(
    auth_client: AsyncClient, admin_user: User, db_session: AsyncSession
) -> None:
    for _ in range(5):
        resp = await auth_client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "wrong"},
        )
        assert resp.status_code == 401

    # 6th attempt — locked
    resp = await auth_client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "Str0ngP@ssword"},
    )
    assert resp.status_code == 423

    await db_session.refresh(admin_user)
    assert admin_user.failed_login_attempts >= 5
    assert admin_user.locked_until is not None


@pytest.mark.asyncio
async def test_login_resets_attempts_on_success(
    auth_client: AsyncClient, admin_user: User, db_session: AsyncSession
) -> None:
    # 2 failed attempts
    for _ in range(2):
        await auth_client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "wrong"},
        )
    # then success
    resp = await auth_client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "Str0ngP@ssword"},
    )
    assert resp.status_code == 200

    await db_session.refresh(admin_user)
    assert admin_user.failed_login_attempts == 0
    assert admin_user.locked_until is None
    assert admin_user.last_login_at is not None


@pytest.mark.asyncio
async def test_login_accepts_username_alias_for_email(
    auth_client: AsyncClient, admin_user: User
) -> None:
    """Legacy clients posting `username` instead of `email` must still work."""
    resp = await auth_client.post(
        "/api/v1/auth/login",
        json={"username": "admin@example.com", "password": "Str0ngP@ssword"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /auth/refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_returns_new_access_token_and_rotates_refresh(
    auth_client: AsyncClient, admin_user: User, db_session: AsyncSession
) -> None:
    login = await auth_client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "Str0ngP@ssword"},
    )
    assert login.status_code == 200

    resp = await auth_client.post("/api/v1/auth/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["user"]["email"] == "admin@example.com"

    # Old refresh revoked, new one issued
    tokens = (await db_session.execute(select(RefreshToken))).scalars().all()
    assert len(tokens) == 2
    revoked = [t for t in tokens if t.revoked_at is not None]
    active = [t for t in tokens if t.revoked_at is None]
    assert len(revoked) == 1
    assert len(active) == 1


@pytest.mark.asyncio
async def test_refresh_without_cookie_returns_401(auth_client: AsyncClient) -> None:
    resp = await auth_client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_with_revoked_token_returns_401(
    auth_client: AsyncClient, admin_user: User, db_session: AsyncSession
) -> None:
    await auth_client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "Str0ngP@ssword"},
    )
    # Manually revoke
    rt = (await db_session.execute(select(RefreshToken))).scalar_one()
    rt.revoked_at = datetime.now(UTC)
    await db_session.commit()

    resp = await auth_client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_with_expired_token_returns_401(
    auth_client: AsyncClient, admin_user: User, db_session: AsyncSession
) -> None:
    await auth_client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "Str0ngP@ssword"},
    )
    rt = (await db_session.execute(select(RefreshToken))).scalar_one()
    rt.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    resp = await auth_client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /auth/logout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_revokes_refresh_token(
    auth_client: AsyncClient, admin_user: User, db_session: AsyncSession
) -> None:
    await auth_client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "Str0ngP@ssword"},
    )

    resp = await auth_client.post("/api/v1/auth/logout")
    assert resp.status_code == 200

    rt = (await db_session.execute(select(RefreshToken))).scalar_one()
    assert rt.revoked_at is not None


@pytest.mark.asyncio
async def test_logout_without_cookie_still_returns_200(auth_client: AsyncClient) -> None:
    resp = await auth_client.post("/api/v1/auth/logout")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_returns_current_user_profile(
    db_session: AsyncSession, admin_user: User
) -> None:
    async def override_db():
        yield db_session

    def override_user():
        return {
            "user_id": str(admin_user.user_id),
            "role": "admin",
            "roles": ["admin"],
            "email": admin_user.email,
        }

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.get(
                "/api/v1/auth/me", headers={"Authorization": "Bearer fake"}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == "admin@example.com"
        assert body["role"] == "admin"
        assert body["is_active"] is True
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_me_returns_401_for_inactive_user(
    db_session: AsyncSession, inactive_user: User
) -> None:
    async def override_db():
        yield db_session

    def override_user():
        return {
            "user_id": str(inactive_user.user_id),
            "role": "operator",
            "roles": ["operator"],
            "email": inactive_user.email,
        }

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.get(
                "/api/v1/auth/me", headers={"Authorization": "Bearer fake"}
            )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_me_returns_401_for_unknown_user(db_session: AsyncSession) -> None:
    async def override_db():
        yield db_session

    def override_user():
        return {
            "user_id": str(uuid.uuid4()),
            "role": "admin",
            "roles": ["admin"],
            "email": "ghost@example.com",
        }

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.get(
                "/api/v1/auth/me", headers={"Authorization": "Bearer fake"}
            )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()
