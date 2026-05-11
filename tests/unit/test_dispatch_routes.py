"""Unit tests for api/routes/dispatch.py — dispatch plan CRUD, schedule, P&L, backtest."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1 — GET /dispatch/plans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_plans_empty(client) -> None:
    resp = await client.get("/api/v1/dispatch/plans", headers={"Authorization": "Bearer test"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["count"] == 0


# ---------------------------------------------------------------------------
# 2 — POST /dispatch/plans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_plan(client, sample_battery) -> None:
    payload = {
        "battery_id": str(sample_battery.battery_id),
        "delivery_date": "2025-06-01",
        "quarter_hour": 0,
        "power_kw": "50.00",
    }
    resp = await client.post(
        "/api/v1/dispatch/plans", json=payload, headers={"Authorization": "Bearer test"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["battery_id"] == str(sample_battery.battery_id)
    assert body["delivery_date"] == "2025-06-01"
    assert body["quarter_hour"] == 0
    assert Decimal(body["power_kw"]) == Decimal("50.00")
    assert "plan_id" in body


@pytest.mark.asyncio
async def test_list_plans_after_create(client, sample_battery) -> None:
    payload = {
        "battery_id": str(sample_battery.battery_id),
        "delivery_date": "2025-07-01",
        "quarter_hour": 4,
        "power_kw": "-30.00",
    }
    await client.post(
        "/api/v1/dispatch/plans", json=payload, headers={"Authorization": "Bearer test"}
    )

    resp = await client.get("/api/v1/dispatch/plans", headers={"Authorization": "Bearer test"})
    body = resp.json()
    assert body["meta"]["count"] == 1


# ---------------------------------------------------------------------------
# 3 — GET /dispatch/plans/{plan_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_plan_not_found(client) -> None:
    resp = await client.get(
        f"/api/v1/dispatch/plans/{uuid.uuid4()}",
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_plan_by_id(client, sample_battery) -> None:
    payload = {
        "battery_id": str(sample_battery.battery_id),
        "delivery_date": "2025-06-15",
        "quarter_hour": 10,
        "power_kw": "100.00",
    }
    create_resp = await client.post(
        "/api/v1/dispatch/plans", json=payload, headers={"Authorization": "Bearer test"}
    )
    plan_id = create_resp.json()["plan_id"]

    resp = await client.get(
        f"/api/v1/dispatch/plans/{plan_id}", headers={"Authorization": "Bearer test"}
    )
    assert resp.status_code == 200
    assert resp.json()["plan_id"] == plan_id


# ---------------------------------------------------------------------------
# 4 — GET /dispatch/schedule/today
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_schedule_today_503_when_no_scheduler(client) -> None:
    with patch("api.main._scheduler", None):
        resp = await client.get(
            "/api/v1/dispatch/schedule/today", headers={"Authorization": "Bearer test"}
        )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_get_schedule_today_404_when_no_schedule(client) -> None:
    mock_scheduler = MagicMock()
    mock_scheduler.get_schedule.return_value = None

    with patch("api.main._scheduler", mock_scheduler):
        resp = await client.get(
            "/api/v1/dispatch/schedule/today", headers={"Authorization": "Bearer test"}
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_schedule_today_returns_schedule(client) -> None:
    mock_schedule = MagicMock()
    mock_schedule.to_dict.return_value = {"date": "2025-06-01", "hours": {}}
    mock_schedule.status = MagicMock(value="executing")

    mock_scheduler = MagicMock()
    mock_scheduler.get_schedule.return_value = mock_schedule

    with patch("api.main._scheduler", mock_scheduler):
        resp = await client.get(
            "/api/v1/dispatch/schedule/today", headers={"Authorization": "Bearer test"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert body["meta"]["status"] == "executing"


# ---------------------------------------------------------------------------
# 5 — POST /dispatch/schedule/force
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_schedule_503_when_no_scheduler(client) -> None:
    with patch("api.main._scheduler", None):
        resp = await client.post(
            "/api/v1/dispatch/schedule/force",
            json={},
            headers={"Authorization": "Bearer test"},
        )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_force_schedule_accepts_with_date(client) -> None:
    mock_scheduler = AsyncMock()
    mock_scheduler.trigger_now = AsyncMock()

    with patch("api.main._scheduler", mock_scheduler):
        resp = await client.post(
            "/api/v1/dispatch/schedule/force",
            json={"delivery_date": "2025-08-01"},
            headers={"Authorization": "Bearer test"},
        )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "accepted"
    assert "2025-08-01" in body["delivery_date"]


@pytest.mark.asyncio
async def test_force_schedule_accepts_without_date(client) -> None:
    mock_scheduler = AsyncMock()
    mock_scheduler.trigger_now = AsyncMock()

    with patch("api.main._scheduler", mock_scheduler):
        resp = await client.post(
            "/api/v1/dispatch/schedule/force",
            json={},
            headers={"Authorization": "Bearer test"},
        )
    assert resp.status_code == 202
    assert resp.json()["status"] == "accepted"


# ---------------------------------------------------------------------------
# 6 — GET /dispatch/pnl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_pnl_503_when_no_scheduler(client) -> None:
    with patch("api.main._scheduler", None):
        resp = await client.get("/api/v1/dispatch/pnl", headers={"Authorization": "Bearer test"})
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_get_pnl_returns_data(client) -> None:
    mock_scheduler = MagicMock()
    mock_scheduler.get_today_pnl.return_value = {
        "realised_pnl_eur": 120.5,
        "projected_pnl_eur": 200.0,
        "completion_pct": 60.0,
    }

    with patch("api.main._scheduler", mock_scheduler):
        resp = await client.get("/api/v1/dispatch/pnl", headers={"Authorization": "Bearer test"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["realised_pnl_eur"] == 120.5
    assert body["meta"]["currency"] == "EUR"


# ---------------------------------------------------------------------------
# 7 — POST /dispatch/backtest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backtest_missing_dates_returns_422(client) -> None:
    resp = await client.post(
        "/api/v1/dispatch/backtest",
        json={"zone": "SUD"},
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_backtest_invalid_date_format_returns_422(client) -> None:
    resp = await client.post(
        "/api/v1/dispatch/backtest",
        json={"date_start": "not-a-date", "date_end": "2025-01-31"},
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_backtest_range_too_long_returns_422(client) -> None:
    resp = await client.post(
        "/api/v1/dispatch/backtest",
        json={"date_start": "2023-01-01", "date_end": "2025-01-01"},
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_backtest_accepted(client) -> None:
    with patch("core.dispatch.backtester.Backtester") as mock_bt_cls:
        mock_bt = AsyncMock()
        mock_bt.simulate = AsyncMock(return_value=MagicMock(to_summary=lambda: {}))
        mock_bt_cls.return_value = mock_bt

        resp = await client.post(
            "/api/v1/dispatch/backtest",
            json={"date_start": "2025-01-01", "date_end": "2025-01-07", "zone": "SUD"},
            headers={"Authorization": "Bearer test"},
        )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["days"] == 7
    assert body["zone"] == "SUD"
    assert "task_id" in body
