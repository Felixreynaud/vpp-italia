"""Unit tests for /api/v1/batteries endpoints."""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from data.models import Battery, BatteryProtocol, BatteryState

AUTH = {"Authorization": "Bearer test"}


async def _create_battery(db_session) -> Battery:
    battery = Battery(
        battery_id=uuid.uuid4(),
        asset_id="IT_DISPATCH_001",
        site_id=uuid.uuid4(),
        name="Dispatch Battery",
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


@pytest.mark.asyncio
async def test_list_batteries_empty(client):
    resp = await client.get("/api/v1/batteries", headers={"Authorization": "Bearer test"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["count"] == 0


@pytest.mark.asyncio
async def test_create_battery(client):
    payload = {
        "asset_id": "IT_BESS_TEST_001",
        "site_id": str(uuid.uuid4()),
        "name": "Test Battery",
        "protocol": "modbus",
        "host": "10.0.0.1",
        "port": 502,
        "capacity_kwh": "1000.00",
        "max_power_kw": "500.00",
        "min_soc_percent": "10.0",
        "max_soc_percent": "90.0",
    }
    resp = await client.post(
        "/api/v1/batteries", json=payload, headers={"Authorization": "Bearer test"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["asset_id"] == "IT_BESS_TEST_001"
    assert body["state"] == "offline"
    assert "battery_id" in body


@pytest.mark.asyncio
async def test_get_battery(client, sample_battery):
    resp = await client.get(
        f"/api/v1/batteries/{sample_battery.battery_id}",
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["battery_id"] == str(sample_battery.battery_id)
    assert body["asset_id"] == sample_battery.asset_id


@pytest.mark.asyncio
async def test_get_battery_not_found(client):
    resp = await client.get(
        f"/api/v1/batteries/{uuid.uuid4()}",
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_battery(client, sample_battery):
    resp = await client.patch(
        f"/api/v1/batteries/{sample_battery.battery_id}",
        json={"name": "Updated Battery Name"},
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Battery Name"


@pytest.mark.asyncio
async def test_list_batteries_with_sample(client, sample_battery):
    resp = await client.get("/api/v1/batteries", headers={"Authorization": "Bearer test"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["count"] == 1
    assert body["data"][0]["battery_id"] == str(sample_battery.battery_id)


# ---------------------------------------------------------------------------
# POST /{battery_id}/dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_command_success(client, db_session) -> None:
    battery = await _create_battery(db_session)

    with patch(
        "connectors.modbus.send_power_setpoint", new=AsyncMock(return_value=str(uuid.uuid4()))
    ):
        resp = await client.post(
            f"/api/v1/batteries/{battery.battery_id}/dispatch",
            json={"power_kw": "100.00"},
            headers=AUTH,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "command_id" in body
    assert body["battery_id"] == str(battery.battery_id)


@pytest.mark.asyncio
async def test_dispatch_command_battery_not_found(client) -> None:
    resp = await client.post(
        f"/api/v1/batteries/{uuid.uuid4()}/dispatch",
        json={"power_kw": "50.00"},
        headers=AUTH,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dispatch_command_exceeds_max_power(client, db_session) -> None:
    battery = await _create_battery(db_session)

    resp = await client.post(
        f"/api/v1/batteries/{battery.battery_id}/dispatch",
        json={"power_kw": "9999.00"},
        headers=AUTH,
    )
    assert resp.status_code == 422
