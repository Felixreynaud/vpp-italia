"""Tests for the Huawei SmartPVMS connector — uses HuaweiSimulator throughout."""

from __future__ import annotations

import asyncio
import time

import pytest

from connectors.huawei.exceptions import HuaweiAPIError, HuaweiAuthError, HuaweiTaskError
from connectors.huawei.models import DispatchSwitch, TaskStatus
from connectors.huawei.simulator import HuaweiSimulator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sim() -> HuaweiSimulator:
    """Simulator with three LUNA2000 plants, one per model."""
    return HuaweiSimulator(plants=[
        ("PLANT_107", "LUNA2000-107kWh"),
        ("PLANT_161", "LUNA2000-161kWh"),
        ("PLANT_215", "LUNA2000-215kWh"),
    ])


@pytest.fixture
async def ready_sim(sim: HuaweiSimulator) -> HuaweiSimulator:
    """Simulator with dispatch mode pre-enabled on all plants."""
    for plant in await sim.get_plant_list():
        await sim.set_dispatch_mode(plant.plant_code)
    return sim


# ---------------------------------------------------------------------------
# 1 — Discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_plant_list(sim: HuaweiSimulator) -> None:
    plants = await sim.get_plant_list()
    assert len(plants) == 3
    codes = {p.plant_code for p in plants}
    assert "PLANT_107" in codes
    assert "PLANT_215" in codes


@pytest.mark.asyncio
async def test_get_plant_list_capacity(sim: HuaweiSimulator) -> None:
    plants = await sim.get_plant_list()
    by_code = {p.plant_code: p for p in plants}
    assert by_code["PLANT_107"].capacity_kwh == pytest.approx(107.0)
    assert by_code["PLANT_215"].capacity_kwh == pytest.approx(215.0)


@pytest.mark.asyncio
async def test_get_device_list(sim: HuaweiSimulator) -> None:
    devices = await sim.get_device_list("PLANT_107")
    assert len(devices) == 1
    assert devices[0].plant_code == "PLANT_107"
    assert "LUNA2000-107kWh" in devices[0].model


# ---------------------------------------------------------------------------
# 2 — Authentification & mode dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_dispatch_mode_returns_request_id(sim: HuaweiSimulator) -> None:
    request_id = await sim.set_dispatch_mode("PLANT_107")
    assert request_id.startswith("MODE_")
    assert len(request_id) > 5


@pytest.mark.asyncio
async def test_charge_fails_without_dispatch_mode(sim: HuaweiSimulator) -> None:
    with pytest.raises(HuaweiAPIError) as exc_info:
        await sim.charge("PLANT_107", power_w=50_000)
    assert exc_info.value.fail_code == 401
    assert "thirdPartyDispatch" in str(exc_info.value)


@pytest.mark.asyncio
async def test_dispatch_mode_idempotent(sim: HuaweiSimulator) -> None:
    await sim.set_dispatch_mode("PLANT_107")
    await sim.set_dispatch_mode("PLANT_107")  # calling twice must not raise
    task = await sim.charge("PLANT_107", power_w=50_000)
    assert task.request_id is not None


# ---------------------------------------------------------------------------
# 3 — Lecture SoC et puissance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_battery_realtime_returns_status(sim: HuaweiSimulator) -> None:
    devices = await sim.get_device_list("PLANT_107")
    device_ids = [d.device_id for d in devices]
    statuses = await sim.get_battery_realtime(device_ids, plant_code="PLANT_107")
    assert len(statuses) == 1
    s = statuses[0]
    assert 0.0 <= s.soc <= 100.0
    assert s.device_id == device_ids[0]


@pytest.mark.asyncio
async def test_get_battery_realtime_soc_bounds(sim: HuaweiSimulator) -> None:
    for plant_code in ["PLANT_107", "PLANT_161", "PLANT_215"]:
        devices = await sim.get_device_list(plant_code)
        statuses = await sim.get_battery_realtime(
            [d.device_id for d in devices], plant_code=plant_code
        )
        for s in statuses:
            assert 0.0 <= s.soc <= 100.0
            assert s.temperature_c is not None
            assert s.voltage_v is not None


@pytest.mark.asyncio
async def test_idle_battery_power_is_zero(sim: HuaweiSimulator) -> None:
    devices = await sim.get_device_list("PLANT_107")
    statuses = await sim.get_battery_realtime(
        [d.device_id for d in devices], plant_code="PLANT_107"
    )
    # No command sent yet — power should be ~0
    assert statuses[0].power_kw == pytest.approx(0.0, abs=0.01)
    assert not statuses[0].is_charging
    assert not statuses[0].is_discharging


# ---------------------------------------------------------------------------
# 4 — Commande charge et vérification task_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_charge_returns_task(ready_sim: HuaweiSimulator) -> None:
    task = await ready_sim.charge("PLANT_107", power_w=50_000)
    assert task.request_id.startswith("TASK_")
    assert task.dispatch_switch == DispatchSwitch.CHARGE
    assert task.status == TaskStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_charge_power_reflected_in_realtime(ready_sim: HuaweiSimulator) -> None:
    await ready_sim.charge("PLANT_107", power_w=50_000)
    devices = await ready_sim.get_device_list("PLANT_107")
    statuses = await ready_sim.get_battery_realtime(
        [d.device_id for d in devices], plant_code="PLANT_107"
    )
    s = statuses[0]
    assert s.is_charging
    assert s.power_kw == pytest.approx(50.0, abs=0.1)  # 50 000 W → 50 kW


@pytest.mark.asyncio
async def test_charge_clamped_to_max_power(ready_sim: HuaweiSimulator) -> None:
    # LUNA2000-107kWh has max_power_kw=108. Request 200 kW.
    await ready_sim.charge("PLANT_107", power_w=200_000)
    devices = await ready_sim.get_device_list("PLANT_107")
    statuses = await ready_sim.get_battery_realtime(
        [d.device_id for d in devices], plant_code="PLANT_107"
    )
    assert statuses[0].power_kw <= 108.0 + 1e-6


@pytest.mark.asyncio
async def test_charge_invalid_power_raises(ready_sim: HuaweiSimulator) -> None:
    with pytest.raises(ValueError, match="power_w > 0"):
        await ready_sim.charge("PLANT_107", power_w=-10_000)


@pytest.mark.asyncio
async def test_charge_task_completes(ready_sim: HuaweiSimulator) -> None:
    task = await ready_sim.charge("PLANT_107", power_w=50_000)
    final = await ready_sim.wait_for_task(task.request_id, "PLANT_107")
    assert final.is_complete
    assert final.status == TaskStatus.COMPLETE


# ---------------------------------------------------------------------------
# 5 — Commande décharge avec target_soc
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discharge_returns_task(ready_sim: HuaweiSimulator) -> None:
    ready_sim.set_soc("PLANT_107", 80.0)
    task = await ready_sim.discharge("PLANT_107", power_w=30_000, target_soc=20.0)
    assert task.request_id.startswith("TASK_")
    assert task.dispatch_switch == DispatchSwitch.DISCHARGE
    assert task.target_soc == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_discharge_power_negative_in_status(ready_sim: HuaweiSimulator) -> None:
    ready_sim.set_soc("PLANT_107", 80.0)
    await ready_sim.discharge("PLANT_107", power_w=30_000)
    devices = await ready_sim.get_device_list("PLANT_107")
    statuses = await ready_sim.get_battery_realtime(
        [d.device_id for d in devices], plant_code="PLANT_107"
    )
    s = statuses[0]
    assert s.is_discharging
    assert s.power_kw < 0


@pytest.mark.asyncio
async def test_discharge_invalid_power_raises(ready_sim: HuaweiSimulator) -> None:
    with pytest.raises(ValueError, match="power_w > 0"):
        await ready_sim.discharge("PLANT_107", power_w=0)


# ---------------------------------------------------------------------------
# 6 — Stop d'urgence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_halts_charge(ready_sim: HuaweiSimulator) -> None:
    await ready_sim.charge("PLANT_107", power_w=80_000)
    # Verify charging before stop
    devices = await ready_sim.get_device_list("PLANT_107")
    statuses_before = await ready_sim.get_battery_realtime(
        [d.device_id for d in devices], plant_code="PLANT_107"
    )
    assert statuses_before[0].power_kw > 0

    await ready_sim.stop("PLANT_107")
    # Rate limit reset for test
    ready_sim._last_realtime.clear()
    statuses_after = await ready_sim.get_battery_realtime(
        [d.device_id for d in devices], plant_code="PLANT_107"
    )
    assert statuses_after[0].power_kw == pytest.approx(0.0, abs=0.01)
    assert statuses_after[0].dispatch_switch == DispatchSwitch.STOP


@pytest.mark.asyncio
async def test_stop_clears_pending_task(ready_sim: HuaweiSimulator) -> None:
    await ready_sim.charge("PLANT_107", power_w=50_000)
    assert "PLANT_107" in ready_sim._pending_tasks

    await ready_sim.stop("PLANT_107")
    assert "PLANT_107" not in ready_sim._pending_tasks


@pytest.mark.asyncio
async def test_stop_allowed_without_dispatch_mode(sim: HuaweiSimulator) -> None:
    # Stop is a safety command — must succeed even without dispatch mode
    task = await sim.stop("PLANT_107")
    assert task.dispatch_switch == DispatchSwitch.STOP


# ---------------------------------------------------------------------------
# 7 — Sécurité : double commande interdite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_double_charge_raises(ready_sim: HuaweiSimulator) -> None:
    await ready_sim.charge("PLANT_107", power_w=50_000)
    with pytest.raises(HuaweiTaskError) as exc_info:
        await ready_sim.charge("PLANT_107", power_w=30_000)
    assert "pending task" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_charge_then_discharge_blocked_until_stop(ready_sim: HuaweiSimulator) -> None:
    await ready_sim.charge("PLANT_107", power_w=50_000)
    with pytest.raises(HuaweiTaskError):
        await ready_sim.discharge("PLANT_107", power_w=30_000)
    await ready_sim.stop("PLANT_107")
    # After stop, discharge should succeed
    task = await ready_sim.discharge("PLANT_107", power_w=30_000)
    assert task.dispatch_switch == DispatchSwitch.DISCHARGE


# ---------------------------------------------------------------------------
# 8 — Rate limiting (erreur 407)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_on_rapid_realtime_calls(sim: HuaweiSimulator) -> None:
    devices = await sim.get_device_list("PLANT_107")
    device_ids = [d.device_id for d in devices]

    # First call — should succeed
    await sim.get_battery_realtime(device_ids, plant_code="PLANT_107")

    # Immediate second call — should raise 407
    with pytest.raises(HuaweiAPIError) as exc_info:
        await sim.get_battery_realtime(device_ids, plant_code="PLANT_107")
    assert exc_info.value.is_rate_limit
    assert exc_info.value.fail_code == 407


@pytest.mark.asyncio
async def test_rate_limit_different_plants_independent(sim: HuaweiSimulator) -> None:
    devs_107 = await sim.get_device_list("PLANT_107")
    devs_161 = await sim.get_device_list("PLANT_161")

    # Call both plants once — no rate limit
    await sim.get_battery_realtime([d.device_id for d in devs_107], plant_code="PLANT_107")
    await sim.get_battery_realtime([d.device_id for d in devs_161], plant_code="PLANT_161")

    # Second call on PLANT_107 → rate limited
    with pytest.raises(HuaweiAPIError) as exc_info:
        await sim.get_battery_realtime([d.device_id for d in devs_107], plant_code="PLANT_107")
    assert exc_info.value.fail_code == 407

    # PLANT_161 should still be callable (independent counter)
    # Reset its timer to simulate passing time
    sim._last_realtime.pop("PLANT_161")
    await sim.get_battery_realtime([d.device_id for d in devs_161], plant_code="PLANT_161")


# ---------------------------------------------------------------------------
# 9 — Modèles de données
# ---------------------------------------------------------------------------


def test_battery_status_charging_flag() -> None:
    from connectors.huawei.models import HuaweiBatteryStatus

    s = HuaweiBatteryStatus(device_id="D1", soc=50.0, power_kw=30.0)
    assert s.is_charging
    assert not s.is_discharging


def test_battery_status_discharging_flag() -> None:
    from connectors.huawei.models import HuaweiBatteryStatus

    s = HuaweiBatteryStatus(device_id="D1", soc=50.0, power_kw=-30.0)
    assert s.is_discharging
    assert not s.is_charging


def test_battery_status_soc_validation() -> None:
    from connectors.huawei.models import HuaweiBatteryStatus
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        HuaweiBatteryStatus(device_id="D1", soc=110.0, power_kw=0.0)

    with pytest.raises(ValidationError):
        HuaweiBatteryStatus(device_id="D1", soc=-5.0, power_kw=0.0)


def test_dispatch_task_complete_property() -> None:
    from connectors.huawei.models import HuaweiDispatchTask

    task = HuaweiDispatchTask(
        request_id="R1",
        plant_code="P1",
        dispatch_switch=DispatchSwitch.CHARGE,
        status=TaskStatus.COMPLETE,
    )
    assert task.is_complete
    assert not task.is_timed_out


def test_huawei_api_error_is_rate_limit() -> None:
    err = HuaweiAPIError("msg", fail_code=407)
    assert err.is_rate_limit

    err2 = HuaweiAPIError("msg", fail_code=429)
    assert err2.is_rate_limit

    err3 = HuaweiAPIError("msg", fail_code=401)
    assert not err3.is_rate_limit


def test_huawei_api_error_from_fail_code() -> None:
    err = HuaweiAPIError.from_fail_code(305)
    assert err.is_session_expired
    assert "305" in str(err)


# ---------------------------------------------------------------------------
# 10 — Intégration simulateur : cycle complet charge → stop → décharge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_cycle(ready_sim: HuaweiSimulator) -> None:
    plant = "PLANT_161"
    ready_sim.set_soc(plant, 50.0)

    # Charge
    task = await ready_sim.charge(plant, power_w=80_000)
    await ready_sim.wait_for_task(task.request_id, plant)

    # Stop
    await ready_sim.stop(plant)

    # Discharge
    task2 = await ready_sim.discharge(plant, power_w=50_000, target_soc=20.0)
    assert task2.dispatch_switch == DispatchSwitch.DISCHARGE

    final = await ready_sim.wait_for_task(task2.request_id, plant)
    assert final.is_complete
