"""Unit tests for connectors/gme.py — GMEClient."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from connectors.gme import GMEClient
from data.schemas import MarketOfferCreate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ENV = {
    "GME_API_BASE_URL": "https://gme.example.com",
    "GME_PARTICIPANT_CODE": "PART_001",
    "GME_API_USERNAME": "user",
    "GME_API_PASSWORD": "secret",
    "GME_SANDBOX_MODE": "true",
}


@pytest.fixture(autouse=True)
def _set_gme_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep env vars available beyond the `with patch.dict(...)` constructor scope:
    GMEClient reads GME_API_USERNAME / GME_API_PASSWORD lazily in `_authenticate()`
    (after the `with` has exited), so the values must remain set throughout the test."""
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)


def make_offer(
    market: str = "MGP",
    direction: str = "UP",
    qh_start: int = 0,
    qh_end: int = 3,
) -> MarketOfferCreate:
    return MarketOfferCreate(
        market=market,
        delivery_date=date(2025, 6, 1),
        quarter_hour_start=qh_start,
        quarter_hour_end=qh_end,
        energy_mwh=Decimal("1.5"),
        price_eur_mwh=Decimal("80.00"),
        direction=direction,
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
    """Return an AsyncMock that behaves as both a direct client and async context manager."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.is_closed = False
    resp = response or make_http_response(200, {"access_token": "tok123"})
    client.post.return_value = resp
    client.delete.return_value = make_http_response(204)

    # Make it usable as `async with httpx.AsyncClient() as client:`
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


# ---------------------------------------------------------------------------
# 1 — Initialisation
# ---------------------------------------------------------------------------


def test_init_reads_env_vars() -> None:
    with patch.dict("os.environ", ENV):
        client = GMEClient()
    assert client._base_url == "https://gme.example.com"
    assert client._participant_code == "PART_001"
    assert client._sandbox is True


def test_init_sandbox_false_when_env_false() -> None:
    with patch.dict("os.environ", {**ENV, "GME_SANDBOX_MODE": "false"}):
        client = GMEClient()
    assert client._sandbox is False


def test_init_sandbox_default_true() -> None:
    env = {k: v for k, v in ENV.items() if k != "GME_SANDBOX_MODE"}
    with patch.dict("os.environ", env, clear=False):
        client = GMEClient()
    assert client._sandbox is True


# ---------------------------------------------------------------------------
# 2 — _build_payload
# ---------------------------------------------------------------------------


def test_build_payload_structure() -> None:
    with patch.dict("os.environ", ENV):
        gme = GMEClient()

    offer = make_offer()
    payload = gme._build_payload(offer)

    assert payload["market"] == "MGP"
    assert payload["deliveryDate"] == "2025-06-01"
    assert payload["quarterHourStart"] == 0
    assert payload["quarterHourEnd"] == 3
    assert payload["energyMWh"] == 1.5
    assert payload["priceEurMWh"] == 80.0
    assert payload["direction"] == "UP"
    assert payload["participantCode"] == "PART_001"


def test_build_payload_none_energy() -> None:
    with patch.dict("os.environ", ENV):
        gme = GMEClient()

    offer = make_offer()
    offer.energy_mwh = None
    payload = gme._build_payload(offer)
    assert payload["energyMWh"] is None


# ---------------------------------------------------------------------------
# 3 — _authenticate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_returns_token() -> None:
    mock_client = make_async_client_mock(
        response=make_http_response(200, {"access_token": "abc123"})
    )

    with patch.dict("os.environ", ENV):
        gme = GMEClient()

    with patch("connectors.gme.httpx.AsyncClient", return_value=mock_client):
        token = await gme._authenticate()

    assert token == "abc123"
    mock_client.post.assert_awaited_once()
    call_args = mock_client.post.call_args
    assert "/auth/token" in call_args.args[0]


@pytest.mark.asyncio
async def test_authenticate_raises_on_http_error() -> None:
    error_resp = make_http_response(401, {"error": "unauthorized"})
    mock_client = make_async_client_mock(response=error_resp)

    with patch.dict("os.environ", ENV):
        gme = GMEClient()

    with (
        patch("connectors.gme.httpx.AsyncClient", return_value=mock_client),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await gme._authenticate()


# ---------------------------------------------------------------------------
# 4 — submit_offer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_offer_sandbox_posts_to_sandbox_endpoint() -> None:
    auth_resp = make_http_response(200, {"access_token": "tok"})
    submit_resp = make_http_response(200, {"offerId": "EXT_001"})

    auth_client = make_async_client_mock(response=auth_resp)
    submit_client = make_async_client_mock(response=submit_resp)

    with patch.dict("os.environ", {**ENV, "GME_SANDBOX_MODE": "true"}):
        gme = GMEClient()

    with patch("connectors.gme.httpx.AsyncClient", return_value=auth_client):
        await gme._get_client()

    # Replace the internal client with our submit mock
    gme._client = submit_client

    offer = make_offer()
    external_id = await gme.submit_offer(offer)

    assert external_id == "EXT_001"
    submit_client.post.assert_awaited_once()
    endpoint = submit_client.post.call_args.args[0]
    assert "/sandbox/" in endpoint


@pytest.mark.asyncio
async def test_submit_offer_production_posts_to_production_endpoint() -> None:
    submit_resp = make_http_response(200, {"offerId": "PROD_001"})
    submit_client = make_async_client_mock(response=submit_resp)

    with patch.dict("os.environ", {**ENV, "GME_SANDBOX_MODE": "false"}):
        gme = GMEClient()

    gme._client = submit_client

    offer = make_offer()
    external_id = await gme.submit_offer(offer)

    assert external_id == "PROD_001"
    endpoint = submit_client.post.call_args.args[0]
    assert "/sandbox/" not in endpoint
    assert endpoint == "/offers"


@pytest.mark.asyncio
async def test_submit_offer_raises_on_http_error() -> None:
    error_resp = make_http_response(500, {"error": "server error"})
    error_client = make_async_client_mock(response=error_resp)

    with patch.dict("os.environ", ENV):
        gme = GMEClient()

    gme._client = error_client

    with pytest.raises(httpx.HTTPStatusError):
        await gme.submit_offer(make_offer())


# ---------------------------------------------------------------------------
# 5 — cancel_offer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_offer_sandbox_deletes_sandbox_endpoint() -> None:
    delete_resp = make_http_response(204)
    cancel_client = make_async_client_mock()
    cancel_client.delete.return_value = delete_resp

    with patch.dict("os.environ", {**ENV, "GME_SANDBOX_MODE": "true"}):
        gme = GMEClient()

    gme._client = cancel_client

    await gme.cancel_offer("EXT_999")

    cancel_client.delete.assert_awaited_once()
    endpoint = cancel_client.delete.call_args.args[0]
    assert "/sandbox/" in endpoint
    assert "EXT_999" in endpoint


@pytest.mark.asyncio
async def test_cancel_offer_production_deletes_production_endpoint() -> None:
    delete_resp = make_http_response(204)
    cancel_client = make_async_client_mock()
    cancel_client.delete.return_value = delete_resp

    with patch.dict("os.environ", {**ENV, "GME_SANDBOX_MODE": "false"}):
        gme = GMEClient()

    gme._client = cancel_client

    await gme.cancel_offer("PROD_999")

    endpoint = cancel_client.delete.call_args.args[0]
    assert "/sandbox/" not in endpoint
    assert "PROD_999" in endpoint


@pytest.mark.asyncio
async def test_cancel_offer_raises_on_http_error() -> None:
    error_resp = make_http_response(404, {"error": "not found"})
    error_client = make_async_client_mock()
    error_client.delete.return_value = error_resp
    error_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "not found", request=MagicMock(), response=error_resp
    )

    with patch.dict("os.environ", ENV):
        gme = GMEClient()

    gme._client = error_client

    with pytest.raises(httpx.HTTPStatusError):
        await gme.cancel_offer("MISSING")


# ---------------------------------------------------------------------------
# 6 — _get_client: caches the client instance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_client_reuses_open_client() -> None:
    auth_resp = make_http_response(200, {"access_token": "tok"})
    auth_client = make_async_client_mock(response=auth_resp)

    with patch.dict("os.environ", ENV):
        gme = GMEClient()

    with patch("connectors.gme.httpx.AsyncClient", return_value=auth_client) as mock_cls:
        await gme._get_client()
        await gme._get_client()

    # First call: 2 instantiations (auth + persistent client). Second call reuses the open client.
    assert mock_cls.call_count == 2


@pytest.mark.asyncio
async def test_get_client_recreates_when_closed() -> None:
    auth_resp = make_http_response(200, {"access_token": "tok"})
    auth_client = make_async_client_mock(response=auth_resp)
    auth_client.is_closed = True  # simulate a closed client

    with patch.dict("os.environ", ENV):
        gme = GMEClient()

    gme._client = auth_client  # pre-set a closed client

    with patch("connectors.gme.httpx.AsyncClient", return_value=auth_client) as mock_cls:
        await gme._get_client()

    # Re-authentication: 2 instantiations (auth + new persistent client)
    assert mock_cls.call_count == 2
