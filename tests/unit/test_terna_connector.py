"""Unit tests for connectors/terna.py — TernaClient."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from connectors.terna import TernaClient
from data.schemas import MarketOfferCreate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ENV = {
    "TERNA_API_BASE_URL": "https://terna.example.com",
    "TERNA_UPCA_CODE": "IT_UPCA_001",
    "TERNA_CLIENT_ID": "client_id",
    "TERNA_CLIENT_SECRET": "secret",
    "TERNA_SANDBOX_MODE": "true",
}


def make_offer(market: str = "MSD") -> MarketOfferCreate:
    return MarketOfferCreate(
        market=market,
        delivery_date=date(2025, 6, 1),
        quarter_hour_start=0,
        quarter_hour_end=3,
        capacity_mw=Decimal("5.000"),
        price_eur_mwh=Decimal("120.00"),
        direction="UP",
    )


def make_http_response(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


def make_async_client_mock(response: MagicMock | None = None) -> AsyncMock:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.is_closed = False
    resp = response or make_http_response(200, {"access_token": "tok123"})
    client.post.return_value = resp
    client.delete.return_value = make_http_response(204)
    client.get.return_value = make_http_response(200, {"signals": []})
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


# ---------------------------------------------------------------------------
# 1 — Initialisation
# ---------------------------------------------------------------------------


def test_init_reads_env_vars() -> None:
    with patch.dict("os.environ", ENV):
        client = TernaClient()
    assert client._base_url == "https://terna.example.com"
    assert client._upca_code == "IT_UPCA_001"
    assert client._sandbox is True


def test_init_sandbox_false_when_env_false() -> None:
    with patch.dict("os.environ", {**ENV, "TERNA_SANDBOX_MODE": "false"}):
        client = TernaClient()
    assert client._sandbox is False


# ---------------------------------------------------------------------------
# 2 — _authenticate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_returns_token() -> None:
    mock_client = make_async_client_mock(
        response=make_http_response(200, {"access_token": "terna_tok"})
    )

    with patch.dict("os.environ", ENV):
        terna = TernaClient()

    with patch("connectors.terna.httpx.AsyncClient", return_value=mock_client):
        token = await terna._authenticate()

    assert token == "terna_tok"
    mock_client.post.assert_awaited_once()
    assert "/oauth/token" in mock_client.post.call_args.args[0]


@pytest.mark.asyncio
async def test_authenticate_raises_on_http_error() -> None:
    error_resp = make_http_response(401, {"error": "unauthorized"})
    mock_client = make_async_client_mock(response=error_resp)

    with patch.dict("os.environ", ENV):
        terna = TernaClient()

    with (
        patch("connectors.terna.httpx.AsyncClient", return_value=mock_client),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await terna._authenticate()


# ---------------------------------------------------------------------------
# 3 — submit_offer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_offer_sandbox_posts_to_sandbox_endpoint() -> None:
    auth_resp = make_http_response(200, {"access_token": "tok"})
    submit_resp = make_http_response(200, {"id": "TERNA_EXT_001"})

    auth_client = make_async_client_mock(response=auth_resp)
    submit_client = make_async_client_mock(response=submit_resp)

    with patch.dict("os.environ", {**ENV, "TERNA_SANDBOX_MODE": "true"}):
        terna = TernaClient()

    with patch("connectors.terna.httpx.AsyncClient", return_value=auth_client):
        await terna._get_client()

    terna._client = submit_client
    external_id = await terna.submit_offer(make_offer())

    assert external_id == "TERNA_EXT_001"
    endpoint = submit_client.post.call_args.args[0]
    assert "sandbox" in endpoint


@pytest.mark.asyncio
async def test_submit_offer_production_posts_to_production_endpoint() -> None:
    submit_resp = make_http_response(200, {"id": "TERNA_PROD_001"})
    submit_client = make_async_client_mock(response=submit_resp)

    with patch.dict("os.environ", {**ENV, "TERNA_SANDBOX_MODE": "false"}):
        terna = TernaClient()

    terna._client = submit_client
    external_id = await terna.submit_offer(make_offer())

    assert external_id == "TERNA_PROD_001"
    endpoint = submit_client.post.call_args.args[0]
    assert "sandbox" not in endpoint


# ---------------------------------------------------------------------------
# 4 — cancel_offer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_offer_sandbox_endpoint() -> None:
    cancel_client = make_async_client_mock()
    cancel_client.delete.return_value = make_http_response(204)

    with patch.dict("os.environ", {**ENV, "TERNA_SANDBOX_MODE": "true"}):
        terna = TernaClient()

    terna._client = cancel_client
    await terna.cancel_offer("TERNA_EXT_001")

    cancel_client.delete.assert_awaited_once()
    endpoint = cancel_client.delete.call_args.args[0]
    assert "sandbox" in endpoint
    assert "TERNA_EXT_001" in endpoint


@pytest.mark.asyncio
async def test_cancel_offer_production_endpoint() -> None:
    cancel_client = make_async_client_mock()
    cancel_client.delete.return_value = make_http_response(204)

    with patch.dict("os.environ", {**ENV, "TERNA_SANDBOX_MODE": "false"}):
        terna = TernaClient()

    terna._client = cancel_client
    await terna.cancel_offer("TERNA_PROD_001")

    endpoint = cancel_client.delete.call_args.args[0]
    assert "sandbox" not in endpoint
    assert "TERNA_PROD_001" in endpoint


# ---------------------------------------------------------------------------
# 5 — get_dispatch_signals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dispatch_signals_returns_list() -> None:
    signals = [{"qh": 0, "mw": 5.0}, {"qh": 1, "mw": 3.0}]
    signals_client = make_async_client_mock()
    signals_client.get.return_value = make_http_response(200, {"signals": signals})

    with patch.dict("os.environ", ENV):
        terna = TernaClient()

    terna._client = signals_client
    result = await terna.get_dispatch_signals("2025-06-01")

    assert result == signals
    signals_client.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_dispatch_signals_empty_list() -> None:
    signals_client = make_async_client_mock()
    signals_client.get.return_value = make_http_response(200, {"signals": []})

    with patch.dict("os.environ", ENV):
        terna = TernaClient()

    terna._client = signals_client
    result = await terna.get_dispatch_signals("2025-06-01")

    assert result == []
