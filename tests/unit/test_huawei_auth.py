"""Unit tests for connectors/huawei/auth.py — HuaweiAuthClient."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from connectors.huawei.auth import HuaweiAuthClient, _CachedToken
from connectors.huawei.exceptions import HuaweiAPIError, HuaweiAuthError


def make_auth() -> HuaweiAuthClient:
    return HuaweiAuthClient(
        domain="fusionsolar.example.com",
        client_id="test_client",
        client_secret="test_secret",
    )


def make_http_response(
    status_code: int = 200,
    json_data: dict | None = None,
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


# ---------------------------------------------------------------------------
# get_token — cache behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_token_fetches_when_no_cache() -> None:
    auth = make_auth()
    mock_resp = make_http_response(200, {"data": {"accessToken": "tok123", "expiresIn": 3600}})

    with patch("connectors.huawei.auth.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        token = await auth.get_token()

    assert token == "tok123"


@pytest.mark.asyncio
async def test_get_token_uses_cached_valid_token() -> None:
    auth = make_auth()
    auth._cache = _CachedToken(
        access_token="cached_tok",
        expires_at=time.monotonic() + 3600,
    )

    with patch("connectors.huawei.auth.httpx.AsyncClient") as mock_cls:
        token = await auth.get_token()
        mock_cls.assert_not_called()

    assert token == "cached_tok"


@pytest.mark.asyncio
async def test_get_token_refetches_expired_token() -> None:
    auth = make_auth()
    auth._cache = _CachedToken(
        access_token="old_tok",
        expires_at=time.monotonic() - 1,
    )
    mock_resp = make_http_response(200, {"data": {"accessToken": "new_tok", "expiresIn": 3600}})

    with patch("connectors.huawei.auth.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        token = await auth.get_token()

    assert token == "new_tok"


@pytest.mark.asyncio
async def test_refresh_token_force_fetches() -> None:
    auth = make_auth()
    auth._cache = _CachedToken(
        access_token="old_tok",
        expires_at=time.monotonic() + 9999,
    )
    mock_resp = make_http_response(200, {"data": {"accessToken": "fresh_tok", "expiresIn": 3600}})

    with patch("connectors.huawei.auth.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        token = await auth.refresh_token()

    assert token == "fresh_tok"
    assert auth._cache is None or auth._cache.access_token == "fresh_tok"


def test_invalidate_clears_cache() -> None:
    auth = make_auth()
    auth._cache = _CachedToken(access_token="tok", expires_at=time.monotonic() + 3600)
    auth.invalidate()
    assert auth._cache is None


# ---------------------------------------------------------------------------
# _fetch — HTTP error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_raises_on_network_error() -> None:
    auth = make_auth()

    with patch("connectors.huawei.auth.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            side_effect=httpx.RequestError("network error", request=MagicMock())
        )
        mock_cls.return_value = mock_client

        with pytest.raises(HuaweiAuthError, match="Network error"):
            await auth._fetch()


@pytest.mark.asyncio
async def test_fetch_raises_on_401() -> None:
    auth = make_auth()
    mock_resp = make_http_response(401)

    with patch("connectors.huawei.auth.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        with pytest.raises(HuaweiAuthError, match="401"):
            await auth._fetch()


@pytest.mark.asyncio
async def test_fetch_raises_on_unexpected_status() -> None:
    auth = make_auth()
    mock_resp = make_http_response(500)

    with patch("connectors.huawei.auth.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        with pytest.raises(HuaweiAuthError, match="500"):
            await auth._fetch()


@pytest.mark.asyncio
async def test_fetch_raises_when_no_token_in_response() -> None:
    auth = make_auth()
    mock_resp = make_http_response(200, {"data": {}})

    with patch("connectors.huawei.auth.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        with pytest.raises(HuaweiAuthError, match="No access_token"):
            await auth._fetch()


@pytest.mark.asyncio
async def test_fetch_uses_alternate_field_names() -> None:
    auth = make_auth()
    mock_resp = make_http_response(200, {"data": {"access_token": "alt_tok", "expires_in": 1800}})

    with patch("connectors.huawei.auth.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        token = await auth._fetch()

    assert token == "alt_tok"


# ---------------------------------------------------------------------------
# _raise_for_fail_code
# ---------------------------------------------------------------------------


def test_raise_for_fail_code_zero_passes() -> None:
    HuaweiAuthClient._raise_for_fail_code({"failCode": 0, "data": {}})


def test_raise_for_fail_code_none_passes() -> None:
    HuaweiAuthClient._raise_for_fail_code({"data": {}})


def test_raise_for_fail_code_305_raises_auth_error() -> None:
    with pytest.raises(HuaweiAuthError):
        HuaweiAuthClient._raise_for_fail_code({"failCode": 305, "message": "session expired"})


def test_raise_for_fail_code_401_raises_auth_error() -> None:
    with pytest.raises(HuaweiAuthError):
        HuaweiAuthClient._raise_for_fail_code({"failCode": 401, "message": "no permission"})


def test_raise_for_fail_code_other_raises_api_error() -> None:
    with pytest.raises(HuaweiAPIError):
        HuaweiAuthClient._raise_for_fail_code({"failCode": 999, "message": "unknown"})
