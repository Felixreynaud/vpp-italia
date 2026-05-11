"""Unit tests for api/routes/metrics.py — health check, battery metrics, P&L."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1 — GET /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_returns_ok(client) -> None:
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "uptime_seconds" in body
    assert "timestamp" in body


@pytest.mark.asyncio
async def test_health_database_degraded_when_no_session_factory(client) -> None:
    with patch("api.dependencies._session_factory", None):
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["database"] == "degraded"
    assert body["batteries_online"] == 0
    assert body["batteries_total"] == 0


@pytest.mark.asyncio
async def test_health_database_ok_with_session_factory(client) -> None:
    mock_session = AsyncMock()
    mock_result_total = MagicMock()
    mock_result_total.scalar_one_or_none.return_value = 5
    mock_result_online = MagicMock()
    mock_result_online.scalar_one_or_none.return_value = 3
    mock_session.execute.side_effect = [mock_result_total, mock_result_online]
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock(return_value=mock_session)

    with patch("api.dependencies._session_factory", mock_factory):
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["database"] == "ok"
    assert body["batteries_total"] == 5
    assert body["batteries_online"] == 3


@pytest.mark.asyncio
async def test_health_environment_from_env(client) -> None:
    with patch("os.getenv", side_effect=lambda k, d=None: "production" if k == "APP_ENV" else d):
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 2 — GET /metrics/batteries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_battery_metrics_returns_prometheus_text(client) -> None:
    resp = await client.get("/api/v1/metrics/batteries")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_battery_metrics_no_db_returns_error_comment(client) -> None:
    with patch("api.dependencies._session_factory", None):
        resp = await client.get("/api/v1/metrics/batteries")
    assert resp.status_code == 200
    # Should contain an error comment line when DB is unavailable
    assert "# ERROR" in resp.text or resp.text.strip() == ""


@pytest.mark.asyncio
async def test_battery_metrics_contains_help_lines_when_batteries_exist(client) -> None:
    mock_battery = MagicMock()
    mock_battery.battery_id = "BAT_001"
    mock_battery.asset_id = "IT_BESS_001"
    mock_battery.protocol = MagicMock(value="modbus")
    mock_battery.state = MagicMock()
    mock_battery.is_active = True

    batteries_result = MagicMock()
    batteries_result.scalars.return_value.all.return_value = [mock_battery]
    readings_result = MagicMock()
    readings_result.__iter__ = MagicMock(return_value=iter([]))

    mock_session = AsyncMock()
    mock_session.execute.side_effect = [batteries_result, readings_result]
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock(return_value=mock_session)

    with patch("api.dependencies._session_factory", mock_factory):
        resp = await client.get("/api/v1/metrics/batteries")

    assert resp.status_code == 200
    assert "# HELP vpp_battery_soc_percent" in resp.text
    assert "vpp_battery_online" in resp.text


# ---------------------------------------------------------------------------
# 3 — GET /metrics/pnl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pnl_metrics_returns_structure(client) -> None:
    resp = await client.get("/api/v1/metrics/pnl")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    data = body["data"]
    assert "today_eur" in data
    assert "week_eur" in data
    assert "month_eur" in data
    assert "projected_today_eur" in data
    assert data["currency"] == "EUR"


@pytest.mark.asyncio
async def test_pnl_metrics_zero_when_no_db(client) -> None:
    with patch("api.dependencies._session_factory", None):
        resp = await client.get("/api/v1/metrics/pnl")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["today_eur"] == 0.0
    assert data["week_eur"] == 0.0
    assert data["month_eur"] == 0.0


@pytest.mark.asyncio
async def test_pnl_metrics_with_db_data(client) -> None:
    mock_row = MagicMock()
    mock_row.today_eur = 150.0
    mock_row.week_eur = 800.0
    mock_row.month_eur = 3200.0

    mock_result = MagicMock()
    mock_result.fetchone.return_value = mock_row

    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_result
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock(return_value=mock_session)

    with (
        patch("api.dependencies._session_factory", mock_factory),
        patch("api.main._scheduler", None),
    ):
        resp = await client.get("/api/v1/metrics/pnl")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["today_eur"] == 150.0
    assert data["week_eur"] == 800.0
    assert data["month_eur"] == 3200.0


@pytest.mark.asyncio
async def test_pnl_metrics_meta_contains_date_boundaries(client) -> None:
    resp = await client.get("/api/v1/metrics/pnl")
    assert resp.status_code == 200
    meta = resp.json()["meta"]
    assert "today_start" in meta
    assert "week_start" in meta
    assert "month_start" in meta
