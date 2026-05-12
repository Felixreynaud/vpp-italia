"""Unit tests for /api/v1/optimize endpoints."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from data.models import Battery, BatteryProtocol, BatteryState

AUTH = {"Authorization": "Bearer test"}
BASE = "/api/v1/optimize"
PRICES_24 = [50.0 + i for i in range(24)]
PROD_24 = [10.0] * 24
CONS_24 = [15.0] * 24


async def _make_battery(db_session, site_id: uuid.UUID) -> Battery:
    battery = Battery(
        battery_id=uuid.uuid4(),
        asset_id="IT_OPT_001",
        site_id=site_id,
        name="Opt Battery",
        protocol=BatteryProtocol.MODBUS,
        host="10.0.0.1",
        port=502,
        capacity_kwh=Decimal("500.00"),
        max_power_kw=Decimal("250.00"),
        min_soc_percent=Decimal("10.0"),
        max_soc_percent=Decimal("90.0"),
        state=BatteryState.IDLE,
        is_active=True,
    )
    db_session.add(battery)
    await db_session.commit()
    await db_session.refresh(battery)
    return battery


def _list_params(site_id: uuid.UUID, **lists: list) -> list:
    """Build httpx params list with repeated keys for list query params."""
    params = [("site_id", str(site_id))]
    for key, values in lists.items():
        for v in values:
            params.append((key, v))
    return params


# ---------------------------------------------------------------------------
# GET /scenarios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_scenarios_returns_list(client) -> None:
    resp = await client.get(f"{BASE}/scenarios", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert all("type" in s and "name" in s for s in data)


@pytest.mark.asyncio
async def test_list_scenarios_has_required_fields(client) -> None:
    resp = await client.get(f"{BASE}/scenarios", headers=AUTH)
    scenario = resp.json()[0]
    for field in ("type", "name", "description", "available", "future", "default_params"):
        assert field in scenario


# ---------------------------------------------------------------------------
# POST /autoconsommation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_autoconsommation_success(client, db_session) -> None:
    site_id = uuid.uuid4()
    await _make_battery(db_session, site_id)

    params = _list_params(
        site_id,
        production_pv_kw=PROD_24,
        consommation_kw=CONS_24,
        prix_mgp=PRICES_24,
    )
    resp = await client.post(f"{BASE}/autoconsommation", params=params, headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    assert "schedule" in body["data"]


@pytest.mark.asyncio
async def test_autoconsommation_wrong_length_returns_422(client, db_session) -> None:
    site_id = uuid.uuid4()
    await _make_battery(db_session, site_id)

    params = _list_params(
        site_id,
        production_pv_kw=[1.0] * 10,
        consommation_kw=CONS_24,
        prix_mgp=PRICES_24,
    )
    resp = await client.post(f"{BASE}/autoconsommation", params=params, headers=AUTH)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_autoconsommation_no_batteries_returns_404(client) -> None:
    params = _list_params(
        uuid.uuid4(),
        production_pv_kw=PROD_24,
        consommation_kw=CONS_24,
        prix_mgp=PRICES_24,
    )
    resp = await client.post(f"{BASE}/autoconsommation", params=params, headers=AUTH)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /arbitrage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arbitrage_success(client, db_session) -> None:
    site_id = uuid.uuid4()
    await _make_battery(db_session, site_id)

    params = _list_params(site_id, prix_mgp=PRICES_24)
    resp = await client.post(f"{BASE}/arbitrage", params=params, headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "revenu_estime_eur" in body["data"]


@pytest.mark.asyncio
async def test_arbitrage_conservateur_mode(client, db_session) -> None:
    site_id = uuid.uuid4()
    await _make_battery(db_session, site_id)

    params = _list_params(site_id, prix_mgp=PRICES_24)
    params.append(("mode", "conservateur"))
    resp = await client.post(f"{BASE}/arbitrage", params=params, headers=AUTH)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_arbitrage_invalid_mode_returns_422(client, db_session) -> None:
    site_id = uuid.uuid4()
    await _make_battery(db_session, site_id)

    params = _list_params(site_id, prix_mgp=PRICES_24)
    params.append(("mode", "turbo"))
    resp = await client.post(f"{BASE}/arbitrage", params=params, headers=AUTH)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_arbitrage_wrong_length_returns_422(client, db_session) -> None:
    site_id = uuid.uuid4()
    await _make_battery(db_session, site_id)

    params = _list_params(site_id, prix_mgp=[50.0] * 12)
    resp = await client.post(f"{BASE}/arbitrage", params=params, headers=AUTH)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /stochastique
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stochastique_success(client, db_session) -> None:
    site_id = uuid.uuid4()
    await _make_battery(db_session, site_id)

    params = _list_params(site_id, prix_mgp_base=PRICES_24)
    params.append(("n_scenarios", 5))
    resp = await client.post(f"{BASE}/stochastique", params=params, headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert "revenu_espere_eur" in body["data"]


@pytest.mark.asyncio
async def test_stochastique_wrong_length_returns_422(client, db_session) -> None:
    site_id = uuid.uuid4()
    await _make_battery(db_session, site_id)

    params = _list_params(site_id, prix_mgp_base=[50.0] * 5)
    resp = await client.post(f"{BASE}/stochastique", params=params, headers=AUTH)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /backtest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backtest_success(client, db_session) -> None:
    site_id = uuid.uuid4()
    await _make_battery(db_session, site_id)

    resp = await client.post(
        f"{BASE}/backtest",
        params={
            "site_id": str(site_id),
            "date_debut": "2025-06-01",
            "date_fin": "2025-06-07",
            "scenario": "arbitrage_mgp",
        },
        headers=AUTH,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "rapport" in body["data"]
    assert "csv" in body["data"]


@pytest.mark.asyncio
async def test_backtest_fin_before_debut_returns_422(client, db_session) -> None:
    site_id = uuid.uuid4()
    await _make_battery(db_session, site_id)

    resp = await client.post(
        f"{BASE}/backtest",
        params={
            "site_id": str(site_id),
            "date_debut": "2025-06-10",
            "date_fin": "2025-06-01",
            "scenario": "arbitrage_mgp",
        },
        headers=AUTH,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_backtest_no_batteries_returns_404(client) -> None:
    resp = await client.post(
        f"{BASE}/backtest",
        params={
            "site_id": str(uuid.uuid4()),
            "date_debut": "2025-06-01",
            "date_fin": "2025-06-07",
            "scenario": "arbitrage_mgp",
        },
        headers=AUTH,
    )
    assert resp.status_code == 404
