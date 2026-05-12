"""Unit tests for /api/v1/markets endpoints."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from data.models import MarketName, MarketOffer, OfferStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AUTH = {"Authorization": "Bearer test"}
BASE = "/api/v1/markets"


def make_offer_payload(market: str = "MGP") -> dict:
    return {
        "market": market,
        "delivery_date": "2025-06-01",
        "quarter_hour_start": 0,
        "quarter_hour_end": 3,
        "energy_mwh": "1.500",
        "price_eur_mwh": "80.00",
        "direction": "UP",
    }


async def _create_offer(db_session, market: str = "MGP", status: str = "submitted") -> MarketOffer:
    offer = MarketOffer(
        offer_id=uuid.uuid4(),
        market=MarketName(market),
        delivery_date="2025-06-01",
        quarter_hour_start=0,
        quarter_hour_end=3,
        energy_mwh=Decimal("1.5"),
        capacity_mw=None,
        price_eur_mwh=Decimal("80.00"),
        direction="UP",
        external_id="EXT_001",
        status=OfferStatus(status),
    )
    db_session.add(offer)
    await db_session.commit()
    await db_session.refresh(offer)
    return offer


# ---------------------------------------------------------------------------
# GET /markets/offers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_offers_empty(client) -> None:
    resp = await client.get(f"{BASE}/offers", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["count"] == 0


@pytest.mark.asyncio
async def test_list_offers_returns_existing(client, db_session) -> None:
    await _create_offer(db_session)
    resp = await client.get(f"{BASE}/offers", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["count"] == 1
    assert body["data"][0]["market"] == "MGP"


@pytest.mark.asyncio
async def test_list_offers_filter_by_market(client, db_session) -> None:
    await _create_offer(db_session, market="MGP")
    await _create_offer(db_session, market="MSD")
    resp = await client.get(f"{BASE}/offers?market=MGP", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["count"] == 1
    assert body["data"][0]["market"] == "MGP"


@pytest.mark.asyncio
async def test_list_offers_filter_by_delivery_date(client, db_session) -> None:
    await _create_offer(db_session)
    resp = await client.get(f"{BASE}/offers?delivery_date=2025-06-01", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["meta"]["count"] == 1

    resp_none = await client.get(f"{BASE}/offers?delivery_date=2099-01-01", headers=AUTH)
    assert resp_none.json()["meta"]["count"] == 0


# ---------------------------------------------------------------------------
# POST /markets/offers — submit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_offer_mgp_routes_to_gme(client) -> None:
    mock_gme = AsyncMock()
    mock_gme.submit_offer.return_value = "EXT_GME_001"

    with patch("connectors.gme.GMEClient", return_value=mock_gme):
        resp = await client.post(f"{BASE}/offers", json=make_offer_payload("MGP"), headers=AUTH)

    assert resp.status_code == 201
    body = resp.json()
    assert body["external_id"] == "EXT_GME_001"
    assert body["market"] == "MGP"
    mock_gme.submit_offer.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_offer_msd_routes_to_terna(client) -> None:
    mock_terna = AsyncMock()
    mock_terna.submit_offer.return_value = "EXT_TERNA_001"

    payload = make_offer_payload("MSD")
    payload.pop("energy_mwh")
    payload["capacity_mw"] = "5.000"

    with patch("connectors.terna.TernaClient", return_value=mock_terna):
        resp = await client.post(f"{BASE}/offers", json=payload, headers=AUTH)

    assert resp.status_code == 201
    assert resp.json()["external_id"] == "EXT_TERNA_001"
    mock_terna.submit_offer.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_offer_unknown_market_returns_422(client) -> None:
    payload = make_offer_payload("UNKNOWN")
    resp = await client.post(f"{BASE}/offers", json=payload, headers=AUTH)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /markets/offers/{offer_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_offer_found(client, db_session) -> None:
    offer = await _create_offer(db_session)
    resp = await client.get(f"{BASE}/offers/{offer.offer_id}", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["offer_id"] == str(offer.offer_id)
    assert body["external_id"] == "EXT_001"


@pytest.mark.asyncio
async def test_get_offer_not_found(client) -> None:
    resp = await client.get(f"{BASE}/offers/{uuid.uuid4()}", headers=AUTH)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /markets/offers/{offer_id}/cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_offer_success(client, db_session) -> None:
    offer = await _create_offer(db_session, market="MGP", status="submitted")
    mock_gme = AsyncMock()

    with patch("connectors.gme.GMEClient", return_value=mock_gme):
        resp = await client.post(f"{BASE}/offers/{offer.offer_id}/cancel", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    mock_gme.cancel_offer.assert_awaited_once_with("EXT_001")


@pytest.mark.asyncio
async def test_cancel_offer_wrong_status_returns_409(client, db_session) -> None:
    offer = await _create_offer(db_session, status="cancelled")
    resp = await client.post(f"{BASE}/offers/{offer.offer_id}/cancel", headers=AUTH)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_cancel_offer_not_found_returns_404(client) -> None:
    resp = await client.post(f"{BASE}/offers/{uuid.uuid4()}/cancel", headers=AUTH)
    assert resp.status_code == 404
