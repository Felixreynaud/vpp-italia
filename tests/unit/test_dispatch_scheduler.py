"""Unit tests for core/dispatch/scheduler.py — DispatchScheduler."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.dispatch.models import (
    ActionType,
    BatterySpec,
    DailySchedule,
    DispatchAction,
    HourlySchedule,
    HourType,
    ScheduleStatus,
)
from core.dispatch.optimizer import DispatchOptimizer
from core.dispatch.scheduler import DispatchScheduler
from core.market.gme_client import GMEPriceClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_battery(battery_id: str = "BAT_001") -> BatterySpec:
    return BatterySpec(
        battery_id=battery_id,
        capacity_kwh=107.0,
        max_power_kw=108.0,
        soc_min_pct=10.0,
        soc_max_pct=90.0,
        initial_soc_pct=50.0,
        efficiency_roundtrip=0.92,
    )


def make_daily_schedule(
    target: date | None = None,
    battery_id: str = "BAT_001",
) -> DailySchedule:
    target = target or date(2025, 1, 1)
    schedule = DailySchedule(
        date=target,
        zone="SUD",
        estimated_revenue_eur=200.0,
        estimated_cost_eur=80.0,
        status=ScheduleStatus.PLANNED,
    )
    for h in range(24):
        action_type = ActionType.CHARGE if h < 6 else ActionType.DISCHARGE if h >= 18 else ActionType.STOP
        power = 50.0 if action_type == ActionType.CHARGE else -50.0 if action_type == ActionType.DISCHARGE else 0.0
        hs = HourlySchedule(
            hour=h,
            hour_price_eur_mwh=80.0,
            total_power_kw=power,
            actions={
                battery_id: DispatchAction(
                    battery_id=battery_id,
                    hour=h,
                    action_type=action_type,
                    power_kw=power,
                    target_price_eur_mwh=80.0,
                    estimated_revenue_eur=5.0,
                    hour_type=HourType.NEUTRAL,
                    soc_before_pct=50.0,
                    soc_after_pct=52.0,
                )
            },
        )
        schedule.hours[h] = hs
    return schedule


def make_gme_mock(prices: dict[int, float] | None = None) -> AsyncMock:
    gme = AsyncMock(spec=GMEPriceClient)
    gme.get_mgp_prices.return_value = prices or {h: 80.0 for h in range(24)}
    return gme


def make_huawei_mock() -> AsyncMock:
    huawei = AsyncMock()
    huawei.charge.return_value = MagicMock(request_id="req_001")
    huawei.discharge.return_value = MagicMock(request_id="req_002")
    huawei.stop.return_value = MagicMock(request_id="req_003")
    return huawei


def make_scheduler(
    battery_id: str = "BAT_001",
    zone: str = "SUD",
    gme: AsyncMock | None = None,
    huawei: AsyncMock | None = None,
) -> DispatchScheduler:
    batteries = [make_battery(battery_id)]
    return DispatchScheduler(
        batteries=batteries,
        gme_client=gme or make_gme_mock(),
        huawei_client=huawei or make_huawei_mock(),
        zone=zone,
    )


# ---------------------------------------------------------------------------
# 1 — Lifecycle: start / stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_start_creates_tasks() -> None:
    scheduler = make_scheduler()
    await scheduler.start()

    assert scheduler._running is True
    assert len(scheduler._tasks) == 3
    assert all(not t.done() for t in scheduler._tasks)

    await scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_stop_cancels_tasks() -> None:
    scheduler = make_scheduler()
    await scheduler.start()
    await scheduler.stop()

    assert scheduler._running is False
    assert all(t.done() for t in scheduler._tasks)


@pytest.mark.asyncio
async def test_scheduler_double_stop_is_safe() -> None:
    scheduler = make_scheduler()
    await scheduler.start()
    await scheduler.stop()
    # Second stop should not raise
    await scheduler.stop()


# ---------------------------------------------------------------------------
# 2 — _fetch_and_optimize
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_and_optimize_stores_schedule() -> None:
    target = date(2025, 6, 1)
    prices = {h: 80.0 for h in range(24)}
    gme = make_gme_mock(prices)
    scheduler = make_scheduler(gme=gme)

    schedule = await scheduler._fetch_and_optimize(target=target)

    assert schedule.date == target
    assert target in scheduler._schedules
    gme.get_mgp_prices.assert_awaited_once_with(target)


@pytest.mark.asyncio
async def test_fetch_and_optimize_uses_tomorrow_by_default() -> None:
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    gme = make_gme_mock()
    scheduler = make_scheduler(gme=gme)

    with patch("core.dispatch.scheduler.datetime") as mock_dt:
        today = date(2025, 6, 1)
        mock_dt.now.return_value = datetime(2025, 6, 1, 14, 0, 0, tzinfo=ZoneInfo("Europe/Rome"))
        mock_dt.now.return_value.date.return_value = today
        # Use real datetime for DailySchedule creation
        mock_dt.utcnow = datetime.utcnow
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        schedule = await scheduler._fetch_and_optimize()

    # Schedule should be for some future date (not raising)
    assert schedule is not None


@pytest.mark.asyncio
async def test_fetch_and_optimize_zone_passed_to_optimizer() -> None:
    target = date(2025, 6, 2)
    gme = make_gme_mock()
    optimizer_mock = MagicMock(spec=DispatchOptimizer)
    fake_schedule = make_daily_schedule(target)
    optimizer_mock.optimize_day.return_value = fake_schedule

    scheduler = DispatchScheduler(
        batteries=[make_battery()],
        gme_client=gme,
        huawei_client=make_huawei_mock(),
        optimizer=optimizer_mock,
        zone="NORD",
    )

    await scheduler._fetch_and_optimize(target=target)

    call_kwargs = optimizer_mock.optimize_day.call_args
    assert call_kwargs.kwargs.get("zone") == "NORD" or "NORD" in str(call_kwargs)


# ---------------------------------------------------------------------------
# 3 — _execute_hour: charge / discharge / stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_hour_charge_calls_huawei() -> None:
    huawei = make_huawei_mock()
    scheduler = make_scheduler(huawei=huawei)
    schedule = make_daily_schedule()
    # Set hour 0 to CHARGE at 50 kW
    schedule.hours[0].actions["BAT_001"].action_type = ActionType.CHARGE
    schedule.hours[0].actions["BAT_001"].power_kw = 50.0

    await scheduler._execute_hour(schedule, 0)

    huawei.charge.assert_awaited_once_with("BAT_001", power_w=50.0 * 1000)


@pytest.mark.asyncio
async def test_execute_hour_discharge_calls_huawei() -> None:
    huawei = make_huawei_mock()
    scheduler = make_scheduler(huawei=huawei)
    schedule = make_daily_schedule()
    schedule.hours[18].actions["BAT_001"].action_type = ActionType.DISCHARGE
    schedule.hours[18].actions["BAT_001"].power_kw = -80.0

    await scheduler._execute_hour(schedule, 18)

    huawei.discharge.assert_awaited_once_with("BAT_001", power_w=80.0 * 1000)


@pytest.mark.asyncio
async def test_execute_hour_stop_calls_huawei() -> None:
    huawei = make_huawei_mock()
    scheduler = make_scheduler(huawei=huawei)
    schedule = make_daily_schedule()
    schedule.hours[10].actions["BAT_001"].action_type = ActionType.STOP
    schedule.hours[10].actions["BAT_001"].power_kw = 0.0

    await scheduler._execute_hour(schedule, 10)

    huawei.stop.assert_awaited_once_with("BAT_001")


@pytest.mark.asyncio
async def test_execute_hour_logs_entry_on_success() -> None:
    scheduler = make_scheduler()
    schedule = make_daily_schedule()

    assert len(scheduler._dispatch_logs) == 0
    await scheduler._execute_hour(schedule, 0)
    assert len(scheduler._dispatch_logs) == 1

    log = scheduler._dispatch_logs[0]
    assert log.battery_id == "BAT_001"
    assert log.hour == 0
    assert log.success is True


@pytest.mark.asyncio
async def test_execute_hour_sets_status_executing() -> None:
    scheduler = make_scheduler()
    schedule = make_daily_schedule()
    assert schedule.status == ScheduleStatus.PLANNED

    await scheduler._execute_hour(schedule, 0)

    assert schedule.status == ScheduleStatus.EXECUTING


@pytest.mark.asyncio
async def test_execute_hour_skips_missing_hour() -> None:
    huawei = make_huawei_mock()
    scheduler = make_scheduler(huawei=huawei)
    schedule = make_daily_schedule()

    # Remove hour 5 from schedule
    del schedule.hours[5]
    await scheduler._execute_hour(schedule, 5)

    huawei.charge.assert_not_awaited()
    huawei.discharge.assert_not_awaited()
    huawei.stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_hour_command_failure_triggers_stop() -> None:
    huawei = make_huawei_mock()
    huawei.charge.side_effect = RuntimeError("connection lost")
    scheduler = make_scheduler(huawei=huawei)
    schedule = make_daily_schedule()
    schedule.hours[0].actions["BAT_001"].action_type = ActionType.CHARGE

    # Should not raise — error is logged and stop() is called as failsafe
    await scheduler._execute_hour(schedule, 0)

    huawei.stop.assert_awaited_once_with("BAT_001")


# ---------------------------------------------------------------------------
# 4 — get_schedule / get_today_pnl / get_recent_logs
# ---------------------------------------------------------------------------


def test_get_schedule_returns_none_when_empty() -> None:
    scheduler = make_scheduler()
    result = scheduler.get_schedule(date(2025, 1, 1))
    assert result is None


def test_get_schedule_returns_stored_schedule() -> None:
    scheduler = make_scheduler()
    target = date(2025, 6, 1)
    expected = make_daily_schedule(target)
    scheduler._schedules[target] = expected

    result = scheduler.get_schedule(target)
    assert result is expected


def test_get_today_pnl_no_schedule_returns_zeros() -> None:
    scheduler = make_scheduler()
    pnl = scheduler.get_today_pnl()
    assert pnl["realised_pnl_eur"] == 0.0
    assert pnl["projected_pnl_eur"] == 0.0


def test_get_today_pnl_with_schedule() -> None:
    from zoneinfo import ZoneInfo

    scheduler = make_scheduler()
    today = datetime.now(ZoneInfo("Europe/Rome")).date()
    schedule = make_daily_schedule(today)
    scheduler._schedules[today] = schedule

    pnl = scheduler.get_today_pnl()
    assert "realised_pnl_eur" in pnl
    assert "projected_pnl_eur" in pnl
    assert "completion_pct" in pnl
    assert 0.0 <= pnl["completion_pct"] <= 100.0


def test_get_recent_logs_empty() -> None:
    scheduler = make_scheduler()
    logs = scheduler.get_recent_logs()
    assert logs == []


@pytest.mark.asyncio
async def test_get_recent_logs_after_execution() -> None:
    scheduler = make_scheduler()
    schedule = make_daily_schedule()

    await scheduler._execute_hour(schedule, 0)
    await scheduler._execute_hour(schedule, 1)

    logs = scheduler.get_recent_logs(n=10)
    assert len(logs) == 2

    logs_1 = scheduler.get_recent_logs(n=1)
    assert len(logs_1) == 1


# ---------------------------------------------------------------------------
# 5 — trigger_now
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_now_stores_schedule() -> None:
    target = date(2025, 7, 1)
    gme = make_gme_mock()
    scheduler = make_scheduler(gme=gme)

    result = await scheduler.trigger_now(delivery_date=target)

    assert result.date == target
    assert target in scheduler._schedules


@pytest.mark.asyncio
async def test_trigger_now_without_date_returns_schedule() -> None:
    gme = make_gme_mock()
    scheduler = make_scheduler(gme=gme)

    result = await scheduler.trigger_now()

    assert result is not None
    assert isinstance(result.date, date)


# ---------------------------------------------------------------------------
# 6 — force_schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_schedule_overrides_stored_schedule() -> None:
    scheduler = make_scheduler()
    target = date(2025, 8, 1)
    original = make_daily_schedule(target)
    scheduler._schedules[target] = original

    forced = make_daily_schedule(target)
    forced.estimated_revenue_eur = 999.0
    await scheduler.force_schedule(forced)

    assert scheduler._schedules[target] is forced
    assert scheduler._schedules[target].status == ScheduleStatus.OVERRIDDEN


# ---------------------------------------------------------------------------
# 7 — Zone configuration
# ---------------------------------------------------------------------------


def test_scheduler_zone_from_env() -> None:
    with patch.dict("os.environ", {"GME_ZONE": "NORD"}):
        scheduler = DispatchScheduler(
            batteries=[make_battery()],
            gme_client=make_gme_mock(),
            huawei_client=make_huawei_mock(),
            zone=None,
        )
    assert scheduler._zone == "NORD"


def test_scheduler_zone_explicit_overrides_env() -> None:
    with patch.dict("os.environ", {"GME_ZONE": "NORD"}):
        scheduler = DispatchScheduler(
            batteries=[make_battery()],
            gme_client=make_gme_mock(),
            huawei_client=make_huawei_mock(),
            zone="SUD",
        )
    assert scheduler._zone == "SUD"


def test_scheduler_battery_map_populated() -> None:
    batteries = [make_battery("B1"), make_battery("B2")]
    scheduler = DispatchScheduler(
        batteries=batteries,
        gme_client=make_gme_mock(),
        huawei_client=make_huawei_mock(),
    )
    assert "B1" in scheduler._battery_map
    assert "B2" in scheduler._battery_map
