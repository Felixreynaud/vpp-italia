"""Huawei SmartPVMS simulator — same interface as HuaweiBatteryClient.

Simulates three LUNA2000 battery models with realistic physical behaviour:
  - LUNA2000-107kWh  (max 108 kW charge/discharge)
  - LUNA2000-161kWh  (max 108 kW)
  - LUNA2000-215kWh  (max 108 kW)

State evolves in real time based on elapsed seconds since the last command,
so tests that call time.sleep() between commands see realistic SoC drift.
"""

from __future__ import annotations

import asyncio
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

from .exceptions import HuaweiAPIError, HuaweiTaskError
from .models import (
    BatteryDevType,
    DispatchSwitch,
    HuaweiBatteryStatus,
    HuaweiDevice,
    HuaweiDispatchTask,
    HuaweiPlant,
    TaskStatus,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# LUNA2000 hardware specs
# ---------------------------------------------------------------------------

LUNA2000_MODELS: dict[str, dict] = {
    "LUNA2000-107kWh": {"capacity_kwh": 107.0, "max_power_kw": 108.0},
    "LUNA2000-161kWh": {"capacity_kwh": 161.0, "max_power_kw": 108.0},
    "LUNA2000-215kWh": {"capacity_kwh": 215.0, "max_power_kw": 108.0},
}

SOC_MIN = 5.0    # Below this the BMS stops discharging
SOC_MAX = 95.0   # Above this the BMS stops charging
EFFICIENCY = 0.95
TEMP_BASE = 25.0
TEMP_NOISE = 0.5


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------


@dataclass
class _BatteryState:
    device_id: str
    plant_code: str
    model: str
    capacity_kwh: float
    max_power_kw: float
    soc: float = 50.0                         # percent
    temperature_c: float = TEMP_BASE
    current_power_kw: float = 0.0             # + charge, - discharge
    dispatch_switch: DispatchSwitch = DispatchSwitch.STOP
    last_update: float = field(default_factory=time.monotonic)

    def advance(self) -> None:
        """Update SoC and temperature based on elapsed time."""
        now = time.monotonic()
        dt_h = (now - self.last_update) / 3600.0
        self.last_update = now

        if self.current_power_kw > 0:
            # Charging: SoC increases, efficiency < 1
            delta_soc = (self.current_power_kw * EFFICIENCY * dt_h / self.capacity_kwh) * 100.0
            self.soc = min(SOC_MAX, self.soc + delta_soc)
            if self.soc >= SOC_MAX:
                self.current_power_kw = 0.0
                self.dispatch_switch = DispatchSwitch.STOP
        elif self.current_power_kw < 0:
            # Discharging: SoC decreases
            delta_soc = (abs(self.current_power_kw) / EFFICIENCY * dt_h / self.capacity_kwh) * 100.0
            self.soc = max(SOC_MIN, self.soc - delta_soc)
            if self.soc <= SOC_MIN:
                self.current_power_kw = 0.0
                self.dispatch_switch = DispatchSwitch.STOP

        # Temperature drifts slightly with load
        load_factor = abs(self.current_power_kw) / self.max_power_kw
        self.temperature_c = TEMP_BASE + load_factor * 8.0 + random.gauss(0, TEMP_NOISE)

    def to_status(self) -> HuaweiBatteryStatus:
        self.advance()
        voltage = 750.0 + (self.soc - 50.0) * 0.8 + random.gauss(0, 0.5)
        current = (self.current_power_kw * 1000.0 / voltage) if voltage else 0.0
        return HuaweiBatteryStatus(
            device_id=self.device_id,
            plant_code=self.plant_code,
            soc=round(self.soc, 2),
            power_kw=round(self.current_power_kw, 3),
            voltage_v=round(voltage, 1),
            current_a=round(current, 2),
            temperature_c=round(self.temperature_c, 1),
            soh=round(100.0 - max(0.0, (50.0 - self.soc) * 0.01), 1),
            status=1 if self.current_power_kw != 0 else 0,
        )


@dataclass
class _TaskRecord:
    request_id: str
    plant_code: str
    dispatch_switch: DispatchSwitch
    power_kw: float
    status: TaskStatus = TaskStatus.IN_PROGRESS
    complete_at: float = field(default_factory=lambda: time.monotonic() + 2.0)


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------


class HuaweiSimulator:
    """Drop-in replacement for HuaweiBatteryClient — no network calls.

    Instantiate with a list of (plant_code, model_name) tuples.
    Each plant gets one battery device of the specified LUNA2000 model.
    """

    def __init__(self, plants: list[tuple[str, str]] | None = None) -> None:
        if plants is None:
            plants = [
                ("PLANT_001", "LUNA2000-107kWh"),
                ("PLANT_002", "LUNA2000-161kWh"),
                ("PLANT_003", "LUNA2000-215kWh"),
            ]
        self._plants: dict[str, HuaweiPlant] = {}
        self._batteries: dict[str, _BatteryState] = {}   # device_id → state
        self._plant_devices: dict[str, list[str]] = {}   # plant_code → [device_ids]
        self._tasks: dict[str, _TaskRecord] = {}
        self._pending_tasks: dict[str, str] = {}          # plant_code → request_id
        self._dispatch_mode_set: set[str] = set()
        self._call_count: dict[str, int] = {}
        self._last_realtime: dict[str, float] = {}

        for plant_code, model_name in plants:
            specs = LUNA2000_MODELS.get(model_name, LUNA2000_MODELS["LUNA2000-107kWh"])
            device_id = f"DEV_{plant_code}_{model_name.replace('-', '_')}"

            self._plants[plant_code] = HuaweiPlant(
                plant_code=plant_code,
                plant_name=f"VPP Plant {plant_code}",
                capacity_kwh=specs["capacity_kwh"],
                capacity_kw=specs["max_power_kw"],
            )
            self._batteries[device_id] = _BatteryState(
                device_id=device_id,
                plant_code=plant_code,
                model=model_name,
                capacity_kwh=specs["capacity_kwh"],
                max_power_kw=specs["max_power_kw"],
                soc=random.uniform(30.0, 70.0),
            )
            self._plant_devices[plant_code] = [device_id]

    # ------------------------------------------------------------------
    # Discovery (mirrors HuaweiBatteryClient)
    # ------------------------------------------------------------------

    async def get_plant_list(self) -> list[HuaweiPlant]:
        await asyncio.sleep(0)
        return list(self._plants.values())

    async def get_device_list(
        self,
        plant_code: str,
        dev_type_id: int = BatteryDevType.BATTERY_UNIT,
    ) -> list[HuaweiDevice]:
        await asyncio.sleep(0)
        device_ids = self._plant_devices.get(plant_code, [])
        devices = []
        for did in device_ids:
            state = self._batteries[did]
            devices.append(HuaweiDevice(
                device_id=did,
                device_name=f"{state.model} @ {plant_code}",
                device_type_id=dev_type_id,
                plant_code=plant_code,
                model=state.model,
            ))
        return devices

    # ------------------------------------------------------------------
    # Real-time KPIs
    # ------------------------------------------------------------------

    async def get_battery_realtime(
        self,
        device_ids: list[str],
        plant_code: str | None = None,
    ) -> list[HuaweiBatteryStatus]:
        await asyncio.sleep(0)
        if plant_code:
            self._check_sim_rate_limit(plant_code)
            self._last_realtime[plant_code] = time.monotonic()
        return [
            self._batteries[did].to_status()
            for did in device_ids
            if did in self._batteries
        ]

    async def get_plant_realtime(self, plant_code: str) -> dict[str, Any]:
        await asyncio.sleep(0)
        self._check_sim_rate_limit(plant_code)
        self._last_realtime[plant_code] = time.monotonic()
        device_ids = self._plant_devices.get(plant_code, [])
        statuses = [self._batteries[d].to_status() for d in device_ids if d in self._batteries]
        total_power = sum(s.power_kw for s in statuses)
        avg_soc = sum(s.soc for s in statuses) / len(statuses) if statuses else 0
        return {"totalPowerKw": total_power, "averageSoc": avg_soc, "deviceCount": len(statuses)}

    def _check_sim_rate_limit(self, plant_code: str) -> None:
        self._call_count[plant_code] = self._call_count.get(plant_code, 0) + 1
        # Simulate Huawei 407 on rapid consecutive calls (> 1 per second in sim)
        last = self._last_realtime.get(plant_code)
        if last and (time.monotonic() - last) < 1.0:
            raise HuaweiAPIError(
                f"[SIM] Rate limit hit for {plant_code}",
                fail_code=407,
            )

    # ------------------------------------------------------------------
    # Control — dispatch mode
    # ------------------------------------------------------------------

    async def set_dispatch_mode(self, plant_code: str) -> str:
        await asyncio.sleep(0)
        if plant_code not in self._plants:
            raise HuaweiAPIError(f"[SIM] Unknown plant: {plant_code}", fail_code=400)
        self._dispatch_mode_set.add(plant_code)
        request_id = f"MODE_{uuid.uuid4().hex[:8].upper()}"
        logger.debug("simulator.dispatch_mode_set", plant_code=plant_code, request_id=request_id)
        return request_id

    # ------------------------------------------------------------------
    # Control — charge / discharge / stop
    # ------------------------------------------------------------------

    async def charge(
        self,
        plant_code: str,
        power_w: float,
        duration_min: int | None = None,
        target_soc: float | None = None,
    ) -> HuaweiDispatchTask:
        if power_w <= 0:
            raise ValueError(f"charge() requires power_w > 0, got {power_w}")
        self._assert_no_pending(plant_code)
        self._require_dispatch_mode(plant_code)
        return await self._send(plant_code, DispatchSwitch.CHARGE, power_w / 1000.0, target_soc)

    async def discharge(
        self,
        plant_code: str,
        power_w: float,
        duration_min: int | None = None,
        target_soc: float | None = None,
    ) -> HuaweiDispatchTask:
        if power_w <= 0:
            raise ValueError(f"discharge() requires power_w > 0, got {power_w}")
        self._assert_no_pending(plant_code)
        self._require_dispatch_mode(plant_code)
        return await self._send(plant_code, DispatchSwitch.DISCHARGE, -power_w / 1000.0, target_soc)

    async def stop(self, plant_code: str) -> HuaweiDispatchTask:
        self._pending_tasks.pop(plant_code, None)
        return await self._send(plant_code, DispatchSwitch.STOP, 0.0, None)

    async def _send(
        self,
        plant_code: str,
        switch: DispatchSwitch,
        power_kw: float,
        target_soc: float | None,
    ) -> HuaweiDispatchTask:
        await asyncio.sleep(0)
        request_id = f"TASK_{uuid.uuid4().hex[:8].upper()}"

        # Apply command to all devices in the plant immediately
        for did in self._plant_devices.get(plant_code, []):
            bat = self._batteries[did]
            bat.advance()

            if switch == DispatchSwitch.STOP:
                bat.current_power_kw = 0.0
            else:
                # Clamp to hardware limits
                clamped = min(abs(power_kw), bat.max_power_kw)
                bat.current_power_kw = math.copysign(clamped, power_kw)

            bat.dispatch_switch = switch
            bat.last_update = time.monotonic()

        record = _TaskRecord(
            request_id=request_id,
            plant_code=plant_code,
            dispatch_switch=switch,
            power_kw=power_kw,
        )
        self._tasks[request_id] = record
        if switch != DispatchSwitch.STOP:
            self._pending_tasks[plant_code] = request_id

        logger.debug(
            "simulator.dispatch",
            plant_code=plant_code,
            switch=switch.name,
            power_kw=power_kw,
            request_id=request_id,
        )
        return HuaweiDispatchTask(
            request_id=request_id,
            plant_code=plant_code,
            dispatch_switch=switch,
            power_w=power_kw * 1000,
            target_soc=target_soc,
            status=TaskStatus.IN_PROGRESS,
        )

    # ------------------------------------------------------------------
    # Task polling
    # ------------------------------------------------------------------

    async def get_task_status(self, request_id: str, plant_code: str) -> HuaweiDispatchTask:
        await asyncio.sleep(0)
        record = self._tasks.get(request_id)
        if not record:
            raise HuaweiTaskError(f"[SIM] Unknown task {request_id}", request_id=request_id)

        # Mark complete after the simulated delay
        if time.monotonic() >= record.complete_at:
            record.status = TaskStatus.COMPLETE
            self._pending_tasks.pop(plant_code, None)

        return HuaweiDispatchTask(
            request_id=request_id,
            plant_code=plant_code,
            dispatch_switch=record.dispatch_switch,
            power_w=record.power_kw * 1000,
            status=record.status,
        )

    async def wait_for_task(
        self,
        request_id: str,
        plant_code: str,
        max_wait: float = 30.0,
        poll_interval: float = 0.1,
    ) -> HuaweiDispatchTask:
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            task = await self.get_task_status(request_id, plant_code)
            if task.is_complete:
                return task
            if task.is_timed_out:
                raise HuaweiTaskError("Task timed out", request_id=request_id)
            await asyncio.sleep(poll_interval)
        raise HuaweiTaskError(f"Task {request_id} did not complete in {max_wait}s", request_id=request_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _require_dispatch_mode(self, plant_code: str) -> None:
        if plant_code not in self._dispatch_mode_set:
            raise HuaweiAPIError(
                f"[SIM] Plant {plant_code} is not in thirdPartyDispatch mode. "
                "Call set_dispatch_mode() first.",
                fail_code=401,
            )

    def _assert_no_pending(self, plant_code: str) -> None:
        pending = self._pending_tasks.get(plant_code)
        if pending:
            raise HuaweiTaskError(
                f"[SIM] Plant {plant_code} has a pending task {pending}",
                request_id=pending,
            )

    # ------------------------------------------------------------------
    # Test helpers (not on the real client)
    # ------------------------------------------------------------------

    def set_soc(self, plant_code: str, soc: float) -> None:
        """Directly set the SoC of all batteries in a plant (test helper)."""
        for did in self._plant_devices.get(plant_code, []):
            self._batteries[did].soc = soc
            self._batteries[did].last_update = time.monotonic()

    def get_soc(self, plant_code: str) -> float:
        """Return average SoC across all batteries in a plant (test helper)."""
        device_ids = self._plant_devices.get(plant_code, [])
        if not device_ids:
            return 0.0
        return sum(self._batteries[d].soc for d in device_ids) / len(device_ids)
