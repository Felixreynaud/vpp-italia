"""Tests for /api/v1/admin/aggregates endpoints + battery assignment."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db
from api.main import app
from data.models import (
    Battery,
    BatteryProtocol,
    BatteryState,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "dev-secret")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")


def _admin_override() -> dict[str, Any]:
    return {
        "user_id": str(uuid.uuid4()),
        "role": "admin",
        "roles": ["admin"],
        "email": "admin@example.com",
    }


def _operator_override() -> dict[str, Any]:
    return {
        "user_id": str(uuid.uuid4()),
        "role": "operator",
        "roles": ["operator"],
        "email": "op@example.com",
    }


@pytest_asyncio.fixture
async def admin_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = _admin_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def operator_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = _operator_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _make_battery(
    db_session: AsyncSession, name: str = "Test bat", is_active: bool = True
) -> Battery:
    bat = Battery(
        asset_id=f"IT_{name.replace(' ', '_')}",
        site_id=uuid.uuid4(),
        name=name,
        protocol=BatteryProtocol.MODBUS,
        host="10.0.0.1",
        port=502,
        capacity_kwh=Decimal("500.00"),
        max_power_kw=Decimal("250.00"),
        state=BatteryState.IDLE,
        is_active=is_active,
    )
    db_session.add(bat)
    await db_session.commit()
    await db_session.refresh(bat)
    return bat


# ---------------------------------------------------------------------------
# Role enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_operator_cannot_list_aggregates(operator_client: AsyncClient) -> None:
    resp = await operator_client.get(
        "/api/v1/admin/aggregates", headers={"Authorization": "Bearer fake"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_operator_cannot_create_aggregate(operator_client: AsyncClient) -> None:
    resp = await operator_client.post(
        "/api/v1/admin/aggregates",
        headers={"Authorization": "Bearer fake"},
        json={"name": "Op test"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_aggregate_minimal(admin_client: AsyncClient) -> None:
    resp = await admin_client.post(
        "/api/v1/admin/aggregates",
        headers={"Authorization": "Bearer fake"},
        json={"name": "Lombardie MGP"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Lombardie MGP"
    assert body["strategy_type"] == "arbitrage_mgp"
    assert body["is_active"] is True
    assert body["batteries"] == []


@pytest.mark.asyncio
async def test_create_aggregate_full(admin_client: AsyncClient) -> None:
    resp = await admin_client.post(
        "/api/v1/admin/aggregates",
        headers={"Authorization": "Bearer fake"},
        json={
            "name": "Pool Pouilles MSD",
            "description": "Batteries de la zone Sud pour services systeme",
            "strategy_type": "msd",
            "target_market": "MSD",
            "target_zone": "SUD",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["strategy_type"] == "msd"
    assert body["target_market"] == "MSD"
    assert body["target_zone"] == "SUD"


@pytest.mark.asyncio
async def test_create_aggregate_duplicate_name_returns_409(admin_client: AsyncClient) -> None:
    await admin_client.post(
        "/api/v1/admin/aggregates",
        headers={"Authorization": "Bearer fake"},
        json={"name": "Dup"},
    )
    resp = await admin_client.post(
        "/api/v1/admin/aggregates",
        headers={"Authorization": "Bearer fake"},
        json={"name": "Dup"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_list_aggregates(admin_client: AsyncClient) -> None:
    await admin_client.post(
        "/api/v1/admin/aggregates",
        headers={"Authorization": "Bearer fake"},
        json={"name": "A1"},
    )
    await admin_client.post(
        "/api/v1/admin/aggregates",
        headers={"Authorization": "Bearer fake"},
        json={"name": "A2"},
    )
    resp = await admin_client.get(
        "/api/v1/admin/aggregates", headers={"Authorization": "Bearer fake"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["count"] == 2
    assert {a["name"] for a in body["data"]} == {"A1", "A2"}


@pytest.mark.asyncio
async def test_get_aggregate_includes_batteries(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    bat = await _make_battery(db_session, "Bat-1")
    create_resp = await admin_client.post(
        "/api/v1/admin/aggregates",
        headers={"Authorization": "Bearer fake"},
        json={"name": "With battery"},
    )
    aggregate_id = create_resp.json()["aggregate_id"]

    # Assign
    assign_resp = await admin_client.patch(
        f"/api/v1/admin/batteries/{bat.battery_id}/aggregate",
        headers={"Authorization": "Bearer fake"},
        json={"aggregate_id": aggregate_id},
    )
    assert assign_resp.status_code == 200

    # Retrieve
    get_resp = await admin_client.get(
        f"/api/v1/admin/aggregates/{aggregate_id}",
        headers={"Authorization": "Bearer fake"},
    )
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert len(body["batteries"]) == 1
    assert body["batteries"][0]["asset_id"] == "IT_Bat-1"


@pytest.mark.asyncio
async def test_update_aggregate(admin_client: AsyncClient) -> None:
    create_resp = await admin_client.post(
        "/api/v1/admin/aggregates",
        headers={"Authorization": "Bearer fake"},
        json={"name": "Initial"},
    )
    aggregate_id = create_resp.json()["aggregate_id"]

    patch_resp = await admin_client.patch(
        f"/api/v1/admin/aggregates/{aggregate_id}",
        headers={"Authorization": "Bearer fake"},
        json={"name": "Renamed", "target_zone": "NORD"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "Renamed"
    assert patch_resp.json()["target_zone"] == "NORD"


@pytest.mark.asyncio
async def test_update_aggregate_duplicate_name_returns_409(admin_client: AsyncClient) -> None:
    a1 = await admin_client.post(
        "/api/v1/admin/aggregates",
        headers={"Authorization": "Bearer fake"},
        json={"name": "First"},
    )
    await admin_client.post(
        "/api/v1/admin/aggregates",
        headers={"Authorization": "Bearer fake"},
        json={"name": "Second"},
    )
    resp = await admin_client.patch(
        f"/api/v1/admin/aggregates/{a1.json()['aggregate_id']}",
        headers={"Authorization": "Bearer fake"},
        json={"name": "Second"},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Battery assignment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_battery_to_aggregate(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    bat = await _make_battery(db_session, "Bat-A")
    create_resp = await admin_client.post(
        "/api/v1/admin/aggregates",
        headers={"Authorization": "Bearer fake"},
        json={"name": "Target"},
    )
    aggregate_id = create_resp.json()["aggregate_id"]

    resp = await admin_client.patch(
        f"/api/v1/admin/batteries/{bat.battery_id}/aggregate",
        headers={"Authorization": "Bearer fake"},
        json={"aggregate_id": aggregate_id},
    )
    assert resp.status_code == 200
    assert resp.json()["aggregate_id"] == aggregate_id


@pytest.mark.asyncio
async def test_unassign_battery_from_aggregate(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    bat = await _make_battery(db_session, "Bat-B")
    create_resp = await admin_client.post(
        "/api/v1/admin/aggregates",
        headers={"Authorization": "Bearer fake"},
        json={"name": "Holder"},
    )
    aggregate_id = create_resp.json()["aggregate_id"]
    await admin_client.patch(
        f"/api/v1/admin/batteries/{bat.battery_id}/aggregate",
        headers={"Authorization": "Bearer fake"},
        json={"aggregate_id": aggregate_id},
    )

    # Unassign
    resp = await admin_client.patch(
        f"/api/v1/admin/batteries/{bat.battery_id}/aggregate",
        headers={"Authorization": "Bearer fake"},
        json={"aggregate_id": None},
    )
    assert resp.status_code == 200
    assert resp.json()["aggregate_id"] is None


@pytest.mark.asyncio
async def test_assign_inactive_battery_returns_400(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    """A battery not under management (is_active=False) cannot be added."""
    bat = await _make_battery(db_session, "Idle", is_active=False)
    create_resp = await admin_client.post(
        "/api/v1/admin/aggregates",
        headers={"Authorization": "Bearer fake"},
        json={"name": "Target2"},
    )
    aggregate_id = create_resp.json()["aggregate_id"]

    resp = await admin_client.patch(
        f"/api/v1/admin/batteries/{bat.battery_id}/aggregate",
        headers={"Authorization": "Bearer fake"},
        json={"aggregate_id": aggregate_id},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_move_battery_between_aggregates(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Reassigning a battery moves it; exclusive membership."""
    bat = await _make_battery(db_session, "Mover")
    a1 = (
        await admin_client.post(
            "/api/v1/admin/aggregates",
            headers={"Authorization": "Bearer fake"},
            json={"name": "Left"},
        )
    ).json()["aggregate_id"]
    a2 = (
        await admin_client.post(
            "/api/v1/admin/aggregates",
            headers={"Authorization": "Bearer fake"},
            json={"name": "Right"},
        )
    ).json()["aggregate_id"]

    await admin_client.patch(
        f"/api/v1/admin/batteries/{bat.battery_id}/aggregate",
        headers={"Authorization": "Bearer fake"},
        json={"aggregate_id": a1},
    )
    await admin_client.patch(
        f"/api/v1/admin/batteries/{bat.battery_id}/aggregate",
        headers={"Authorization": "Bearer fake"},
        json={"aggregate_id": a2},
    )

    # Left should be empty, Right should have the battery
    left = await admin_client.get(
        f"/api/v1/admin/aggregates/{a1}", headers={"Authorization": "Bearer fake"}
    )
    right = await admin_client.get(
        f"/api/v1/admin/aggregates/{a2}", headers={"Authorization": "Bearer fake"}
    )
    assert left.json()["batteries"] == []
    assert len(right.json()["batteries"]) == 1


@pytest.mark.asyncio
async def test_assign_to_unknown_aggregate_returns_404(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    bat = await _make_battery(db_session, "Stranded")
    resp = await admin_client.patch(
        f"/api/v1/admin/batteries/{bat.battery_id}/aggregate",
        headers={"Authorization": "Bearer fake"},
        json={"aggregate_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Delete cascade behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_aggregate_frees_batteries(
    admin_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Deleting an aggregate should set its batteries' aggregate_id to NULL,
    not delete the batteries themselves."""
    bat = await _make_battery(db_session, "Bat-Survivor")
    create_resp = await admin_client.post(
        "/api/v1/admin/aggregates",
        headers={"Authorization": "Bearer fake"},
        json={"name": "Doomed"},
    )
    aggregate_id = create_resp.json()["aggregate_id"]
    await admin_client.patch(
        f"/api/v1/admin/batteries/{bat.battery_id}/aggregate",
        headers={"Authorization": "Bearer fake"},
        json={"aggregate_id": aggregate_id},
    )

    del_resp = await admin_client.delete(
        f"/api/v1/admin/aggregates/{aggregate_id}",
        headers={"Authorization": "Bearer fake"},
    )
    assert del_resp.status_code == 204

    # Battery still exists, but aggregate_id is NULL
    await db_session.refresh(bat)
    remaining = await db_session.execute(
        select(Battery).where(Battery.battery_id == bat.battery_id)
    )
    survivor = remaining.scalar_one()
    assert survivor.aggregate_id is None


@pytest.mark.asyncio
async def test_delete_unknown_aggregate_returns_404(admin_client: AsyncClient) -> None:
    resp = await admin_client.delete(
        f"/api/v1/admin/aggregates/{uuid.uuid4()}",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404
