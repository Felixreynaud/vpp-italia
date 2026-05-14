"""Tests for MGP price model, service idempotence, and endpoint."""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models import MGPPrice, MGPZone

# ---------------------------------------------------------------------------
# Model & uniqueness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mgp_price_insert_basic(db_session: AsyncSession) -> None:
    row = MGPPrice(
        delivery_date="2026-05-15",
        hour=10,
        zone=MGPZone.NORD,
        price_eur_mwh=Decimal("85.50"),
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)

    assert row.id is not None
    assert row.zone == MGPZone.NORD
    assert row.price_eur_mwh == Decimal("85.50")


@pytest.mark.asyncio
async def test_mgp_price_unique_constraint(db_session: AsyncSession) -> None:
    """Same (date, hour, zone) should be rejected at the DB level."""
    from sqlalchemy.exc import IntegrityError

    db_session.add(
        MGPPrice(
            delivery_date="2026-05-15", hour=10, zone=MGPZone.NORD, price_eur_mwh=Decimal("85.50")
        )
    )
    await db_session.commit()

    db_session.add(
        MGPPrice(
            delivery_date="2026-05-15", hour=10, zone=MGPZone.NORD, price_eur_mwh=Decimal("100.00")
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
async def test_mgp_price_same_date_different_zone_ok(db_session: AsyncSession) -> None:
    """Different zones on the same hour/date are independent rows."""
    db_session.add_all(
        [
            MGPPrice(
                delivery_date="2026-05-15",
                hour=10,
                zone=MGPZone.NORD,
                price_eur_mwh=Decimal("85.50"),
            ),
            MGPPrice(
                delivery_date="2026-05-15",
                hour=10,
                zone=MGPZone.SUD,
                price_eur_mwh=Decimal("80.00"),
            ),
        ]
    )
    await db_session.commit()

    count = (await db_session.execute(select(MGPPrice))).scalars().all()
    assert len(count) == 2


# ---------------------------------------------------------------------------
# Service idempotence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mgp_service_upsert_skips_duplicates(db_session: AsyncSession) -> None:
    """Calling _upsert_rows twice with overlapping rows must not duplicate."""
    from core.market.mgp_service import MGPService

    service = MGPService(db_session)
    rows = [
        {
            "delivery_date": "2026-05-15",
            "hour": 0,
            "zone": MGPZone.NORD,
            "price_eur_mwh": Decimal("45.00"),
        },
        {
            "delivery_date": "2026-05-15",
            "hour": 1,
            "zone": MGPZone.NORD,
            "price_eur_mwh": Decimal("42.00"),
        },
    ]

    inserted_first = await service._upsert_rows(rows, "2026-05-15")
    assert inserted_first == 2

    # Second call with the same data + one new row
    rows_second = rows + [
        {
            "delivery_date": "2026-05-15",
            "hour": 2,
            "zone": MGPZone.NORD,
            "price_eur_mwh": Decimal("40.00"),
        }
    ]
    inserted_second = await service._upsert_rows(rows_second, "2026-05-15")
    assert inserted_second == 1

    total = (await db_session.execute(select(MGPPrice))).scalars().all()
    assert len(total) == 3


# ---------------------------------------------------------------------------
# Endpoint /markets/mgp/prices
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_endpoint_returns_fallback_when_db_empty(client: AsyncClient) -> None:
    """No rows in DB → 24 mock prices returned, source='mock-no-data'."""
    resp = await client.get(
        "/api/v1/markets/mgp/prices",
        params={"zone": "NORD", "delivery_date": "2026-05-15"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["source"] == "mock-no-data"
    assert len(body["data"]["prices"]) == 24
    assert body["data"]["zone"] == "NORD"
    assert body["data"]["delivery_date"] == "2026-05-15"


@pytest.mark.asyncio
async def test_endpoint_returns_db_rows_when_present(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """With rows in DB, source='gme-db' and the real values are returned."""
    db_session.add_all(
        [
            MGPPrice(
                delivery_date="2026-05-15",
                hour=h,
                zone=MGPZone.NORD,
                price_eur_mwh=Decimal(str(50 + h)),
            )
            for h in range(24)
        ]
    )
    await db_session.commit()

    resp = await client.get(
        "/api/v1/markets/mgp/prices",
        params={"zone": "NORD", "delivery_date": "2026-05-15"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["source"] == "gme-db"
    assert len(body["data"]["prices"]) == 24
    assert body["data"]["prices"][0]["price_eur_mwh"] == 50.0
    assert body["data"]["prices"][23]["price_eur_mwh"] == 73.0


@pytest.mark.asyncio
async def test_endpoint_isolates_zones(client: AsyncClient, db_session: AsyncSession) -> None:
    """Querying SUD when only NORD is in DB → fallback mock."""
    db_session.add_all(
        [
            MGPPrice(
                delivery_date="2026-05-15",
                hour=h,
                zone=MGPZone.NORD,
                price_eur_mwh=Decimal(str(50 + h)),
            )
            for h in range(24)
        ]
    )
    await db_session.commit()

    resp = await client.get(
        "/api/v1/markets/mgp/prices",
        params={"zone": "SUD", "delivery_date": "2026-05-15"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["source"] == "mock-no-data"
    assert body["data"]["zone"] == "SUD"


@pytest.mark.asyncio
async def test_endpoint_zones_lists_all(client: AsyncClient) -> None:
    """/mgp/zones returns the 8 codes (7 zones + PUN)."""
    resp = await client.get(
        "/api/v1/markets/mgp/zones",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["count"] == 8
    codes = {z["code"] for z in body["data"]}
    assert codes == {"NORD", "CNOR", "CSUD", "SUD", "CALA", "SARD", "SICI", "PUN"}
