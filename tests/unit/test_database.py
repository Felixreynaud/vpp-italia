"""Unit tests for data/database.py — TimescaleDB helpers."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from data.database import get_aggregate_readings, get_latest_readings, setup_timescaledb

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session(execute_result: MagicMock | None = None) -> AsyncMock:
    session = AsyncMock()
    if execute_result is not None:
        session.execute.return_value = execute_result
    else:
        result = MagicMock()
        result.fetchall.return_value = []
        session.execute.return_value = result
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


def make_row(**kwargs: object) -> MagicMock:
    row = MagicMock()
    row._mapping = kwargs
    return row


# ---------------------------------------------------------------------------
# 1 — setup_timescaledb
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_timescaledb_executes_sql() -> None:
    session = make_session()

    await setup_timescaledb(session)

    session.execute.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_setup_timescaledb_commits_on_success() -> None:
    session = make_session()

    await setup_timescaledb(session)

    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_setup_timescaledb_rolls_back_on_error() -> None:
    session = make_session()
    session.execute.side_effect = RuntimeError("timescaledb not installed")

    await setup_timescaledb(session)

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_setup_timescaledb_does_not_raise_on_error() -> None:
    session = make_session()
    session.execute.side_effect = Exception("any DB error")

    # Should swallow the exception (logged as warning)
    await setup_timescaledb(session)


# ---------------------------------------------------------------------------
# 2 — get_latest_readings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_latest_readings_returns_empty_when_no_rows() -> None:
    result = MagicMock()
    result.__iter__ = MagicMock(return_value=iter([]))
    session = make_session(result)

    rows = await get_latest_readings(session, ["BAT_001"])

    assert rows == []
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_latest_readings_passes_battery_ids() -> None:
    result = MagicMock()
    result.__iter__ = MagicMock(return_value=iter([]))
    session = make_session(result)

    await get_latest_readings(session, ["BAT_001", "BAT_002"])

    call_kwargs = session.execute.call_args
    params = (
        call_kwargs.args[1] if len(call_kwargs.args) > 1 else call_kwargs.kwargs.get("params", {})
    )
    assert "BAT_001" in str(params) or "battery_ids" in str(params)


@pytest.mark.asyncio
async def test_get_latest_readings_maps_rows_to_dicts() -> None:
    now = datetime(2025, 6, 1, 12, 0, 0)
    row = make_row(
        battery_id="BAT_001",
        time=now,
        soc_percent=75.0,
        power_kw=50.0,
        voltage_v=800.0,
        temperature_c=25.0,
        state="charging",
    )
    result = MagicMock()
    result.__iter__ = MagicMock(return_value=iter([row]))
    session = make_session(result)

    rows = await get_latest_readings(session, ["BAT_001"])

    assert len(rows) == 1
    assert rows[0]["battery_id"] == "BAT_001"
    assert rows[0]["soc_percent"] == 75.0
    assert rows[0]["power_kw"] == 50.0


@pytest.mark.asyncio
async def test_get_latest_readings_multiple_batteries() -> None:
    rows_data = [
        make_row(
            battery_id="BAT_001",
            time=datetime(2025, 6, 1),
            soc_percent=50.0,
            power_kw=0.0,
            voltage_v=800.0,
            temperature_c=22.0,
            state="idle",
        ),
        make_row(
            battery_id="BAT_002",
            time=datetime(2025, 6, 1),
            soc_percent=80.0,
            power_kw=-30.0,
            voltage_v=800.0,
            temperature_c=24.0,
            state="discharging",
        ),
    ]
    result = MagicMock()
    result.__iter__ = MagicMock(return_value=iter(rows_data))
    session = make_session(result)

    rows = await get_latest_readings(session, ["BAT_001", "BAT_002"])

    assert len(rows) == 2
    battery_ids = {r["battery_id"] for r in rows}
    assert battery_ids == {"BAT_001", "BAT_002"}


# ---------------------------------------------------------------------------
# 3 — get_aggregate_readings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_aggregate_readings_returns_empty_when_no_rows() -> None:
    result = MagicMock()
    result.__iter__ = MagicMock(return_value=iter([]))
    session = make_session(result)

    rows = await get_aggregate_readings(session, "BAT_001", "2025-06-01", "2025-06-02")

    assert rows == []
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_aggregate_readings_passes_correct_params() -> None:
    result = MagicMock()
    result.__iter__ = MagicMock(return_value=iter([]))
    session = make_session(result)

    await get_aggregate_readings(
        session, "BAT_001", "2025-06-01T00:00:00", "2025-06-01T23:59:59", bucket="1 hour"
    )

    call_kwargs = session.execute.call_args
    params = call_kwargs.args[1] if len(call_kwargs.args) > 1 else {}
    params_str = str(params)
    assert "BAT_001" in params_str
    assert "2025-06-01" in params_str


@pytest.mark.asyncio
async def test_get_aggregate_readings_default_bucket() -> None:
    result = MagicMock()
    result.__iter__ = MagicMock(return_value=iter([]))
    session = make_session(result)

    await get_aggregate_readings(session, "BAT_001", "2025-06-01", "2025-06-02")

    call_kwargs = session.execute.call_args
    params = call_kwargs.args[1] if len(call_kwargs.args) > 1 else {}
    # Default bucket is "15 minutes"
    assert "15 minutes" in str(params)


@pytest.mark.asyncio
async def test_get_aggregate_readings_maps_rows_to_dicts() -> None:
    now = datetime(2025, 6, 1, 12, 0, 0)
    row = make_row(
        bucket=now,
        battery_id="BAT_001",
        avg_soc=65.0,
        avg_power_kw=25.0,
        min_power_kw=0.0,
        max_power_kw=50.0,
        avg_temp_c=23.5,
    )
    result = MagicMock()
    result.__iter__ = MagicMock(return_value=iter([row]))
    session = make_session(result)

    rows = await get_aggregate_readings(session, "BAT_001", "2025-06-01", "2025-06-02")

    assert len(rows) == 1
    assert rows[0]["battery_id"] == "BAT_001"
    assert rows[0]["avg_soc"] == 65.0
    assert rows[0]["min_power_kw"] == 0.0
    assert rows[0]["max_power_kw"] == 50.0
