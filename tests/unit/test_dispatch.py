"""Tests for the dispatch engine — optimizer, backtester, GME client.

All tests use synthetic price data (no real API calls).
The GME prices for 2025-01-01 are taken from historical public data
(approximate values — actual GME archives confirm the shape).
"""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from core.dispatch.backtester import Backtester
from core.dispatch.models import ActionType, BatterySpec, HourType
from core.dispatch.optimizer import DispatchOptimizer
from core.market.gme_client import GMEPriceClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_battery(
    battery_id: str = "BAT_001",
    capacity_kwh: float = 107.0,
    max_power_kw: float = 108.0,
    initial_soc: float = 50.0,
) -> BatterySpec:
    return BatterySpec(
        battery_id=battery_id,
        capacity_kwh=capacity_kwh,
        max_power_kw=max_power_kw,
        soc_min_pct=10.0,
        soc_max_pct=90.0,
        initial_soc_pct=initial_soc,
        efficiency_roundtrip=0.92,
    )


# Approximate MGP SUD prices on 2025-01-01 (EUR/MWh)
# Source: GME historical archives — shape typical of a winter holiday day
PRICES_2025_01_01: dict[int, float] = {
    0: 58.3,  1: 51.2,  2: 47.8,  3: 44.1,  4: 42.5,  5: 43.8,
    6: 52.4,  7: 68.9,  8: 89.2,  9: 102.4, 10: 98.7, 11: 95.1,
    12: 88.6, 13: 82.3, 14: 78.9, 15: 80.2, 16: 85.7, 17: 110.3,
    18: 138.5, 19: 145.2, 20: 132.8, 21: 112.4, 22: 88.1, 23: 68.7,
}

# Flat prices — no arbitrage opportunity
PRICES_FLAT: dict[int, float] = {h: 80.0 for h in range(24)}

# High spread — clear peak/off-peak separation
PRICES_HIGH_SPREAD: dict[int, float] = {
    **{h: 30.0 for h in range(0, 6)},    # night off-peak
    **{h: 80.0 for h in range(6, 16)},   # daytime neutral
    **{h: 180.0 for h in range(16, 22)}, # evening peak
    **{h: 50.0 for h in range(22, 24)},  # late neutral
}


# ---------------------------------------------------------------------------
# 1 — GME client: price fetching and fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gme_client_returns_24_hours() -> None:
    from core.dispatch.models import HourlyPrice

    client = GMEPriceClient(zone="SUD")
    fake_fallback = [
        HourlyPrice(hour=h, price_eur_mwh=80.0, zone="SUD", market="MGP", date=date(2025, 1, 1))
        for h in range(24)
    ]
    # Raise so the except-branch triggers _fallback_prices
    with patch.object(client, "_fetch_mgp", new=AsyncMock(side_effect=RuntimeError("mocked API failure"))):
        with patch.object(client, "_fallback_prices", new=AsyncMock(return_value=fake_fallback)):
            prices = await client.get_mgp_prices(date(2025, 1, 1))

    assert len(prices) == 24
    assert all(0 <= h <= 23 for h in prices)


@pytest.mark.asyncio
async def test_gme_client_cache_avoids_double_fetch() -> None:
    client = GMEPriceClient(zone="SUD")
    from core.dispatch.models import HourlyPrice

    fake_prices = [HourlyPrice(hour=h, price_eur_mwh=80.0, zone="SUD", market="MGP") for h in range(24)]
    fetch_mock = AsyncMock(return_value=fake_prices)

    with patch.object(client, "_fetch_mgp", new=fetch_mock):
        await client.get_mgp_prices(date(2025, 1, 2))
        await client.get_mgp_prices(date(2025, 1, 2))  # second call — should use cache

    assert fetch_mock.call_count == 1


@pytest.mark.asyncio
async def test_gme_client_pun_index_is_mean() -> None:
    client = GMEPriceClient(zone="SUD")
    target = date(2025, 1, 3)
    cache_key = f"mgp:{target}:SUD"

    from core.dispatch.models import HourlyPrice

    client._cache[cache_key] = [
        HourlyPrice(hour=h, price_eur_mwh=100.0, zone="SUD", market="MGP")
        for h in range(24)
    ]
    pun = await client.get_pun_index(target)
    assert pun == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_gme_client_zone_prices_independent() -> None:
    client = GMEPriceClient(zone="SUD")
    from core.dispatch.models import HourlyPrice

    def make_cache(zone: str, price: float) -> list:
        return [HourlyPrice(hour=h, price_eur_mwh=price, zone=zone, market="MGP") for h in range(24)]

    client._cache["mgp:2025-01-04:SUD"] = make_cache("SUD", 90.0)
    client._cache["mgp:2025-01-04:NORD"] = make_cache("NORD", 70.0)

    sud_prices = await client.get_zone_prices("SUD", date(2025, 1, 4))
    nord_prices = await client.get_zone_prices("NORD", date(2025, 1, 4))

    assert sud_prices[0] == pytest.approx(90.0)
    assert nord_prices[0] == pytest.approx(70.0)


# ---------------------------------------------------------------------------
# 2 — Optimizer: hour classification
# ---------------------------------------------------------------------------


def test_optimizer_classifies_peak_offpeak() -> None:
    opt = DispatchOptimizer(threshold=0.5)
    classified = opt._classify_hours(PRICES_HIGH_SPREAD)

    # Evening peak hours (16-21) should be PEAK
    for h in range(16, 22):
        assert classified[h].hour_type == HourType.PEAK, f"Hour {h} should be PEAK"

    # Night hours (0-5) should be OFF_PEAK
    for h in range(0, 6):
        assert classified[h].hour_type == HourType.OFF_PEAK, f"Hour {h} should be OFF_PEAK"


def test_optimizer_flat_prices_all_neutral() -> None:
    opt = DispatchOptimizer(threshold=0.5)
    classified = opt._classify_hours(PRICES_FLAT)
    for h, hp in classified.items():
        assert hp.hour_type == HourType.NEUTRAL, f"Hour {h} should be NEUTRAL with flat prices"


def test_optimizer_threshold_sensitivity() -> None:
    # With high threshold, fewer hours classified as peak/offpeak
    opt_loose = DispatchOptimizer(threshold=2.0)
    opt_tight = DispatchOptimizer(threshold=0.3)

    classified_loose = opt_loose._classify_hours(PRICES_2025_01_01)
    classified_tight = opt_tight._classify_hours(PRICES_2025_01_01)

    peak_loose = sum(1 for h in classified_loose.values() if h.hour_type == HourType.PEAK)
    peak_tight = sum(1 for h in classified_tight.values() if h.hour_type == HourType.PEAK)

    assert peak_loose <= peak_tight, "Looser threshold should produce fewer or equal peak hours"


# ---------------------------------------------------------------------------
# 3 — Optimizer: optimize_day with 2025-01-01 prices
# ---------------------------------------------------------------------------


def test_optimize_day_returns_24_hours() -> None:
    opt = DispatchOptimizer()
    battery = make_battery()
    schedule = opt.optimize_day(PRICES_2025_01_01, [battery], date(2025, 1, 1))

    assert len(schedule.hours) == 24
    assert schedule.date == date(2025, 1, 1)


def test_optimize_day_soc_constraints_respected() -> None:
    opt = DispatchOptimizer()
    battery = make_battery(initial_soc=50.0)
    schedule = opt.optimize_day(PRICES_2025_01_01, [battery])

    for h, hs in schedule.hours.items():
        action = hs.actions.get(battery.battery_id)
        if action:
            assert action.soc_after_pct >= battery.soc_min_pct - 0.1, (
                f"Hour {h}: SoC {action.soc_after_pct:.2f}% below minimum {battery.soc_min_pct}%"
            )
            assert action.soc_after_pct <= battery.soc_max_pct + 0.1, (
                f"Hour {h}: SoC {action.soc_after_pct:.2f}% above maximum {battery.soc_max_pct}%"
            )


def test_optimize_day_positive_pnl_with_spread() -> None:
    """With a clear price spread, the optimizer should produce positive P&L."""
    opt = DispatchOptimizer(threshold=0.3)
    battery = make_battery(initial_soc=50.0)
    schedule = opt.optimize_day(PRICES_HIGH_SPREAD, [battery])

    assert schedule.estimated_pnl_eur > 0, (
        f"Expected positive P&L with high spread, got {schedule.estimated_pnl_eur}"
    )


def test_optimize_day_zero_pnl_flat_prices() -> None:
    """With flat prices, there is no arbitrage — P&L should be zero."""
    opt = DispatchOptimizer(threshold=0.5)
    battery = make_battery()
    schedule = opt.optimize_day(PRICES_FLAT, [battery])

    assert schedule.estimated_pnl_eur == pytest.approx(0.0), (
        f"Expected zero P&L with flat prices, got {schedule.estimated_pnl_eur}"
    )


def test_optimize_day_power_within_limits() -> None:
    opt = DispatchOptimizer()
    battery = make_battery(max_power_kw=108.0)
    schedule = opt.optimize_day(PRICES_2025_01_01, [battery])

    for h, hs in schedule.hours.items():
        action = hs.actions.get(battery.battery_id)
        if action:
            assert abs(action.power_kw) <= battery.max_power_kw + 1e-6, (
                f"Hour {h}: power {abs(action.power_kw):.2f} kW exceeds max {battery.max_power_kw} kW"
            )


def test_optimize_day_discharge_at_peak() -> None:
    """The battery should discharge at least some peak hours with 2025-01-01 prices."""
    opt = DispatchOptimizer(threshold=0.5)
    battery = make_battery(initial_soc=80.0)
    schedule = opt.optimize_day(PRICES_2025_01_01, [battery])

    discharge_hours = [
        h for h, hs in schedule.hours.items()
        if hs.actions.get(battery.battery_id, None) is not None
        and hs.actions[battery.battery_id].power_kw < 0
    ]
    assert len(discharge_hours) > 0, "Expected at least one discharge hour on 2025-01-01"


def test_optimize_day_charge_at_offpeak() -> None:
    """The battery should charge at least some off-peak hours."""
    opt = DispatchOptimizer(threshold=0.5)
    battery = make_battery(initial_soc=20.0)
    schedule = opt.optimize_day(PRICES_2025_01_01, [battery])

    charge_hours = [
        h for h, hs in schedule.hours.items()
        if hs.actions.get(battery.battery_id, None) is not None
        and hs.actions[battery.battery_id].power_kw > 0
    ]
    assert len(charge_hours) > 0, "Expected at least one charge hour on 2025-01-01"


def test_optimize_day_multiple_batteries_independent() -> None:
    """Each battery should be scheduled independently."""
    opt = DispatchOptimizer()
    bat1 = make_battery("BAT_001", initial_soc=80.0)  # starts full -> mainly discharge
    bat2 = make_battery("BAT_002", initial_soc=20.0)  # starts empty -> mainly charge

    schedule = opt.optimize_day(PRICES_2025_01_01, [bat1, bat2])

    actions_bat1 = [hs.actions[bat1.battery_id] for hs in schedule.hours.values() if bat1.battery_id in hs.actions]
    actions_bat2 = [hs.actions[bat2.battery_id] for hs in schedule.hours.values() if bat2.battery_id in hs.actions]

    assert len(actions_bat1) == 24
    assert len(actions_bat2) == 24


# ---------------------------------------------------------------------------
# 4 — Backtester: 7-day simulation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backtester_7_days() -> None:
    """Simulate 7 days using patched price data — no real API calls."""
    battery = make_battery()

    # Patch GMEPriceClient to return our synthetic prices for every date
    with patch.object(
        GMEPriceClient,
        "get_mgp_prices",
        new=AsyncMock(return_value=PRICES_2025_01_01),
    ):
        gme = GMEPriceClient(zone="SUD")
        backtester = Backtester(gme_client=gme, zone="SUD")
        report = await backtester.simulate(
            date_start=date(2025, 1, 1),
            date_end=date(2025, 1, 7),
            batteries=[battery],
        )

    assert len(report.daily_results) == 7
    assert report.date_start == date(2025, 1, 1)
    assert report.date_end == date(2025, 1, 7)


@pytest.mark.asyncio
async def test_backtester_report_has_required_fields() -> None:
    with patch.object(GMEPriceClient, "get_mgp_prices", new=AsyncMock(return_value=PRICES_HIGH_SPREAD)):
        gme = GMEPriceClient(zone="SUD")
        backtester = Backtester(gme_client=gme)
        report = await backtester.simulate(
            date_start=date(2025, 1, 1),
            date_end=date(2025, 1, 3),
            batteries=[make_battery()],
        )

    summary = report.to_summary()
    for key in ("total_pnl_eur", "avg_daily_pnl_eur", "total_cycles", "best_day", "worst_day"):
        assert key in summary, f"Missing field: {key}"


@pytest.mark.asyncio
async def test_backtester_positive_pnl_high_spread() -> None:
    with patch.object(GMEPriceClient, "get_mgp_prices", new=AsyncMock(return_value=PRICES_HIGH_SPREAD)):
        gme = GMEPriceClient(zone="SUD")
        backtester = Backtester(gme_client=gme, optimizer=DispatchOptimizer(threshold=0.3))
        report = await backtester.simulate(
            date_start=date(2025, 1, 1),
            date_end=date(2025, 1, 7),
            batteries=[make_battery(initial_soc=50.0)],
        )

    assert report.total_pnl_eur > 0, "Expected positive total P&L over 7 days with high price spread"


@pytest.mark.asyncio
async def test_backtester_csv_export() -> None:
    with patch.object(GMEPriceClient, "get_mgp_prices", new=AsyncMock(return_value=PRICES_FLAT)):
        gme = GMEPriceClient(zone="SUD")
        backtester = Backtester(gme_client=gme)
        report = await backtester.simulate(
            date_start=date(2025, 1, 1),
            date_end=date(2025, 1, 3),
            batteries=[make_battery()],
        )

    csv_output = backtester.to_csv(report)
    lines = [l for l in csv_output.strip().splitlines() if l]
    assert lines[0].startswith("date"), "CSV must start with header row"
    assert len(lines) == 4, "Header + 3 data rows expected"  # header + 3 days


@pytest.mark.asyncio
async def test_backtester_json_export_is_valid() -> None:
    import json

    with patch.object(GMEPriceClient, "get_mgp_prices", new=AsyncMock(return_value=PRICES_FLAT)):
        gme = GMEPriceClient(zone="SUD")
        backtester = Backtester(gme_client=gme)
        report = await backtester.simulate(
            date_start=date(2025, 1, 1),
            date_end=date(2025, 1, 2),
            batteries=[make_battery()],
        )

    json_str = backtester.to_json(report)
    parsed = json.loads(json_str)
    assert "total_pnl_eur" in parsed
    assert "daily_results" in parsed
    assert len(parsed["daily_results"]) == 2


@pytest.mark.asyncio
async def test_backtester_invalid_date_range() -> None:
    gme = GMEPriceClient(zone="SUD")
    backtester = Backtester(gme_client=gme)
    with pytest.raises(ValueError, match="date_end"):
        await backtester.simulate(
            date_start=date(2025, 1, 7),
            date_end=date(2025, 1, 1),
            batteries=[make_battery()],
        )


# ---------------------------------------------------------------------------
# 5 — Model properties
# ---------------------------------------------------------------------------


def test_daily_schedule_pnl_property() -> None:
    from core.dispatch.models import DailySchedule, ScheduleStatus

    s = DailySchedule(
        date=date(2025, 1, 1),
        zone="SUD",
        estimated_revenue_eur=150.0,
        estimated_cost_eur=80.0,
        status=ScheduleStatus.PLANNED,
    )
    assert s.estimated_pnl_eur == pytest.approx(70.0)


def test_daily_schedule_to_dict_shape() -> None:
    opt = DispatchOptimizer()
    schedule = opt.optimize_day(PRICES_2025_01_01, [make_battery()], date(2025, 1, 1))
    d = schedule.to_dict()

    assert "date" in d
    assert "estimated_pnl_eur" in d
    assert "hours" in d
    assert len(d["hours"]) == 24
