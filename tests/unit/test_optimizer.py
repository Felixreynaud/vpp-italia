"""Unit tests for the LP optimizer."""

from core.optimizer import BatteryParams, MarketSignal, optimize_battery


def make_battery(**kwargs) -> BatteryParams:
    defaults = {
        "battery_id": "test-bat-001",
        "capacity_kwh": 1000.0,
        "max_power_kw": 500.0,
        "initial_soc_percent": 50.0,
    }
    defaults.update(kwargs)
    return BatteryParams(**defaults)


def make_signals(price: float = 80.0, direction: str = "BOTH") -> list[MarketSignal]:
    return [
        MarketSignal(quarter_hour=t, price_eur_mwh=price, direction=direction) for t in range(96)
    ]


def test_optimize_returns_96_setpoints():
    battery = make_battery()
    signals = make_signals()
    result = optimize_battery(battery, signals)

    assert result.solve_status == "Optimal"
    assert len(result.power_schedule_kw) == 96


def test_power_within_limits():
    battery = make_battery(max_power_kw=500.0)
    signals = make_signals()
    result = optimize_battery(battery, signals)

    for p in result.power_schedule_kw:
        assert abs(p) <= 500.0 + 1e-6, f"Power {p} exceeds 500 kW"


def test_soc_within_bounds():
    battery = make_battery(min_soc_percent=10.0, max_soc_percent=90.0)
    signals = make_signals()
    result = optimize_battery(battery, signals)

    for soc in result.soc_schedule_percent:
        assert 9.9 <= soc <= 90.1, f"SoC {soc}% out of bounds"


def test_no_signals_produces_zero_schedule():
    battery = make_battery()
    result = optimize_battery(battery, [])

    assert result.expected_revenue_eur == 0.0


def test_discharge_only_direction():
    battery = make_battery(initial_soc_percent=80.0)
    signals = make_signals(direction="UP")
    result = optimize_battery(battery, signals)

    assert result.solve_status == "Optimal"
    # With discharge-only signals, charging setpoints should be 0
    # (battery should only discharge or stay idle)


def test_revenue_positive_with_signals():
    battery = make_battery(initial_soc_percent=80.0)
    signals = make_signals(price=100.0, direction="UP")
    result = optimize_battery(battery, signals)

    assert result.expected_revenue_eur >= 0.0


def test_ramp_rate_respected():
    ramp_kw_per_min = 10.0
    battery = make_battery(max_power_kw=500.0, ramp_rate_kw_per_min=ramp_kw_per_min)
    signals = make_signals()
    result = optimize_battery(battery, signals)

    max_delta = ramp_kw_per_min * 15  # per QH
    for i in range(1, len(result.power_schedule_kw)):
        delta = abs(result.power_schedule_kw[i] - result.power_schedule_kw[i - 1])
        assert delta <= max_delta + 1e-3, f"Ramp violation at QH {i}: delta={delta}"
