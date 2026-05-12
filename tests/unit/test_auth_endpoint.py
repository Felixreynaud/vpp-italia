"""Unit tests for POST /api/v1/auth/login."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from jose import jwt


@pytest.mark.asyncio
async def test_login_dev_mode_returns_token(client) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "anything"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_token_contains_sub_and_roles(client) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "pw"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    with patch.dict(
        os.environ, {"JWT_SECRET_KEY": "dev-secret-change-in-prod-openssl-rand-hex-32"}
    ):
        payload = jwt.decode(
            token, "dev-secret-change-in-prod-openssl-rand-hex-32", algorithms=["HS256"]
        )

    assert payload["sub"] == "admin"
    assert "admin" in payload["roles"]


@pytest.mark.asyncio
async def test_login_operator_gets_operator_role(client) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "operator", "password": "pw"},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    payload = jwt.decode(
        token,
        "dev-secret-change-in-prod-openssl-rand-hex-32",
        algorithms=["HS256"],
    )
    assert payload["roles"] == ["operator"]


@pytest.mark.asyncio
async def test_login_production_mode_wrong_password_returns_401(client) -> None:
    with patch.dict(os.environ, {"APP_ENV": "production"}):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrong"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_production_mode_unknown_user_returns_401(client) -> None:
    with patch.dict(os.environ, {"APP_ENV": "production"}):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "unknown", "password": "pw"},
        )
    assert resp.status_code == 401
