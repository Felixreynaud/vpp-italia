"""Unit tests for data/schemas.py — Pydantic validators and field constraints."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from data.schemas import (
    BatteryCreate,
    BatteryUpdate,
    DispatchCommand,
    DispatchPlanCreate,
    MarketOfferCreate,
)

# ---------------------------------------------------------------------------
# BatteryCreate — max_soc_above_min validator
# ---------------------------------------------------------------------------


def _battery_payload(**overrides: object) -> dict:
    base: dict = {
        "asset_id": "IT_BESS_001",
        "site_id": "12345678-1234-5678-1234-567812345678",
        "name": "Test Battery",
        "protocol": "modbus",
        "host": "192.168.1.1",
        "port": 502,
        "capacity_kwh": "1000.00",
        "max_power_kw": "500.00",
    }
    base.update(overrides)
    return base


def test_battery_create_valid() -> None:
    b = BatteryCreate(**_battery_payload())
    assert b.min_soc_percent == Decimal("10.0")
    assert b.max_soc_percent == Decimal("90.0")


def test_battery_create_custom_soc_bounds() -> None:
    b = BatteryCreate(**_battery_payload(min_soc_percent="20.0", max_soc_percent="80.0"))
    assert b.min_soc_percent == Decimal("20.0")
    assert b.max_soc_percent == Decimal("80.0")


def test_battery_create_max_soc_equal_min_raises() -> None:
    with pytest.raises(ValidationError, match="max_soc_percent must be greater"):
        BatteryCreate(**_battery_payload(min_soc_percent="50.0", max_soc_percent="50.0"))


def test_battery_create_max_soc_below_min_raises() -> None:
    with pytest.raises(ValidationError, match="max_soc_percent must be greater"):
        BatteryCreate(**_battery_payload(min_soc_percent="60.0", max_soc_percent="40.0"))


def test_battery_create_port_out_of_range_raises() -> None:
    with pytest.raises(ValidationError):
        BatteryCreate(**_battery_payload(port=0))
    with pytest.raises(ValidationError):
        BatteryCreate(**_battery_payload(port=65536))


def test_battery_create_negative_capacity_raises() -> None:
    with pytest.raises(ValidationError):
        BatteryCreate(**_battery_payload(capacity_kwh="-100.00"))


def test_battery_create_with_ramp_rate() -> None:
    b = BatteryCreate(**_battery_payload(ramp_rate_kw_per_min="5.0"))
    assert b.ramp_rate_kw_per_min == Decimal("5.0")


# ---------------------------------------------------------------------------
# BatteryUpdate — partial update schema
# ---------------------------------------------------------------------------


def test_battery_update_all_none() -> None:
    u = BatteryUpdate()
    assert u.name is None
    assert u.host is None
    assert u.is_active is None


def test_battery_update_partial() -> None:
    u = BatteryUpdate(name="New Name", is_active=False)
    assert u.name == "New Name"
    assert u.is_active is False


# ---------------------------------------------------------------------------
# DispatchCommand
# ---------------------------------------------------------------------------


def test_dispatch_command_defaults() -> None:
    cmd = DispatchCommand(power_kw=Decimal("100.0"))
    assert cmd.duration_minutes == 15
    assert cmd.reason is None


def test_dispatch_command_discharge_negative() -> None:
    cmd = DispatchCommand(power_kw=Decimal("-200.0"), duration_minutes=30, reason="test")
    assert cmd.power_kw == Decimal("-200.0")
    assert cmd.duration_minutes == 30


def test_dispatch_command_duration_bounds() -> None:
    with pytest.raises(ValidationError):
        DispatchCommand(power_kw=Decimal("100.0"), duration_minutes=0)
    with pytest.raises(ValidationError):
        DispatchCommand(power_kw=Decimal("100.0"), duration_minutes=61)


# ---------------------------------------------------------------------------
# DispatchPlanCreate — delivery_date_str validator
# ---------------------------------------------------------------------------


def test_dispatch_plan_create_date_serialized_as_string() -> None:
    plan = DispatchPlanCreate(
        battery_id="12345678-1234-5678-1234-567812345678",
        delivery_date=date(2025, 6, 1),
        quarter_hour=0,
        power_kw=Decimal("100.0"),
    )
    assert plan.delivery_date == "2025-06-01"


def test_dispatch_plan_create_quarter_hour_bounds() -> None:
    with pytest.raises(ValidationError):
        DispatchPlanCreate(
            battery_id="12345678-1234-5678-1234-567812345678",
            delivery_date=date(2025, 6, 1),
            quarter_hour=-1,
            power_kw=Decimal("100.0"),
        )
    with pytest.raises(ValidationError):
        DispatchPlanCreate(
            battery_id="12345678-1234-5678-1234-567812345678",
            delivery_date=date(2025, 6, 1),
            quarter_hour=96,
            power_kw=Decimal("100.0"),
        )


# ---------------------------------------------------------------------------
# MarketOfferCreate — end_after_start validator
# ---------------------------------------------------------------------------


def _offer_payload(**overrides: object) -> dict:
    base: dict = {
        "market": "MGP",
        "delivery_date": "2025-06-01",
        "quarter_hour_start": 0,
        "quarter_hour_end": 3,
        "energy_mwh": "1.500",
        "price_eur_mwh": "80.00",
        "direction": "UP",
    }
    base.update(overrides)
    return base


def test_market_offer_create_valid() -> None:
    offer = MarketOfferCreate(**_offer_payload())
    assert offer.quarter_hour_end == 3
    assert offer.direction == "UP"


def test_market_offer_create_end_before_start_raises() -> None:
    with pytest.raises(ValidationError, match="quarter_hour_end must be"):
        MarketOfferCreate(**_offer_payload(quarter_hour_start=10, quarter_hour_end=5))


def test_market_offer_create_end_equals_start_allowed() -> None:
    offer = MarketOfferCreate(**_offer_payload(quarter_hour_start=4, quarter_hour_end=4))
    assert offer.quarter_hour_end == 4


def test_market_offer_create_invalid_direction_raises() -> None:
    with pytest.raises(ValidationError):
        MarketOfferCreate(**_offer_payload(direction="SIDEWAYS"))


def test_market_offer_create_direction_down() -> None:
    offer = MarketOfferCreate(**_offer_payload(direction="DOWN"))
    assert offer.direction == "DOWN"


def test_market_offer_create_direction_both() -> None:
    offer = MarketOfferCreate(**_offer_payload(direction="BOTH"))
    assert offer.direction == "BOTH"


def test_market_offer_capacity_mw_for_msd() -> None:
    offer = MarketOfferCreate(**_offer_payload(market="MSD", energy_mwh=None, capacity_mw="5.000"))
    assert offer.capacity_mw == Decimal("5.000")
    assert offer.energy_mwh is None


def test_market_offer_negative_price_raises() -> None:
    with pytest.raises(ValidationError):
        MarketOfferCreate(**_offer_payload(price_eur_mwh="-1.00"))
