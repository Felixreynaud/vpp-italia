"""Huawei SmartPVMS simulator — fully in-memory, no real API calls.

Used in unit tests and local demos to exercise all connector code paths
without network access.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from dataclasses import dataclass, field

from connectors.huawei.exceptions import HuaweiAPIError, HuaweiAuthError, HuaweiTaskError
from connectors.huawei.models import (
    DispatchSwitch,
    HuaweiBatteryStatus,
    HuaweiDevice,
    HuaweiDispatchTask,
    HuaweiPlant,
    TaskStatus,
)

# ---------------------------------------------------------------------------
# Simulator constants
# ---------------------------------------------------------------------------

LATENCY_MS = 5  # Simulated API round-trip latency
SOC_MIN = 10.0
SOC_MAX = 90.0
EFFICIENCY = 0.92
TEMP_BASE = 25.0
TEMP_NOISE = 0.3
REALTIME_RATE_LIMIT_S = (
    300.0  # 5-minute minimum between real-time calls per plant (Huawei NBI limit)
)

# LUNA2000 model catalogue: name → (capacity_kwh, max_power_kw)
LUNA2000_MODELS: dict[str, tuple[float, float]] = {
    "LUNA2000-5kWh": (5.0, 2.5),
    "LUNA2000-10kWh": (10.0, 5.0),
    "LUNA2000-15kWh": (15.0, 7.5),
    "LUNA2000-30kWh": (30.0, 15.0),
    "LUNA2000-107kWh": (107.0, 100.0),
    "LUNA2000-161kWh": (161.0, 150.0),
    "LUNA2000-215kWh": (215.0, 200.0),
}


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
    soc: float = 50.0  # percent
    temperature_c: float = TEMP_BASE
    current_power_kw: float = 0.0  # + charge, - discharge
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
            dispatch_switch=int(self.dispatch_switch),
        )


@dataclass
class _TaskRecord:
    request_id: str
    plant_code: str
    dispatch_switch: DispatchSwitch
    power_kw: float
    duration_min: int
    target_soc: float | None
    created_at: float = field(default_factory=time.monotonic)
    status: TaskStatus = TaskStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------


class HuaweiSimulator:
    """Drop-in replacement for HuaweiClient in tests.

    Accepts a list of (plant_code, model) pairs at construction. Each plant
    gets one battery device whose specs come from the LUNA2000_MODELS catalogue.
    """

    def __init__(self, plants: list[tuple[str, str]] | None = None) -> None:
        self._plants: dict[str, HuaweiPlant] = {}
        self._batteries: dict[str, _BatteryState] = {}  # device_id → state
        self._tasks: dict[str, _TaskRecord] = {}  # request_id → task
        self._dispatch_mode: set[str] = set()  # plant codes with dispatch enabled
        self._pending_tasks: dict[str, str] = {}  # plant_code → active request_id
        self._last_realtime: dict[str, float] = {}  # plant_code → monotonic timestamp

        for plant_code, model in plants or []:
            self._add_plant(plant_code, model)

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------

    def _add_plant(self, plant_code: str, model: str) -> None:
        capacity_kwh, max_power_kw = LUNA2000_MODELS.get(model, (100.0, 50.0))
        plant = HuaweiPlant(
            plant_code=plant_code,
            plant_name=f"{model} @ {plant_code}",
            capacity_kwh=capacity_kwh,
        )
        self._plants[plant_code] = plant

        device_id = f"DEV_{plant_code}"
        self._batteries[device_id] = _BatteryState(
            device_id=device_id,
            plant_code=plant_code,
            model=model,
            capacity_kwh=capacity_kwh,
            max_power_kw=max_power_kw,
        )

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    async def get_plant_list(self) -> list[HuaweiPlant]:
        await asyncio.sleep(LATENCY_MS / 1000)
        return list(self._plants.values())

    async def get_device_list(self, plant_code: str) -> list[HuaweiDevice]:
        await asyncio.sleep(LATENCY_MS / 1000)
        if plant_code not in self._plants:
            raise HuaweiAPIError(f"Plant {plant_code} not found", status_code=404)

        devices = [
            HuaweiDevice(
                device_id=bat.device_id,
                device_name=f"{bat.model} unit",
                device_type_id=39,
                plant_code=plant_code,
                model=bat.model,
            )
            for bat in self._batteries.values()
            if bat.plant_code == plant_code
        ]
        return devices

    # ------------------------------------------------------------------
    # Real-time status
    # ------------------------------------------------------------------

    async def get_battery_status(
        self,
        plant_code: str,
        device_ids: list[str] | None = None,
    ) -> list[HuaweiBatteryStatus]:
        """Return current KPIs for all (or specified) batteries in the plant."""
        await asyncio.sleep(LATENCY_MS / 1000)
        if plant_code not in self._plants:
            raise HuaweiAPIError(f"Plant {plant_code} not found", status_code=404)

        results = []
        for bat in self._batteries.values():
            if bat.plant_code != plant_code:
                continue
            if device_ids and bat.device_id not in device_ids:
                continue
            results.append(bat.to_status())
        return results

    # ------------------------------------------------------------------
    # Dispatch mode
    # ------------------------------------------------------------------

    async def set_dispatch_mode(self, plant_code: str) -> str:
        """Enable remote dispatch for the plant. Returns a request_id."""
        await asyncio.sleep(LATENCY_MS / 1000)
        if plant_code not in self._plants:
            raise HuaweiAPIError(f"Plant {plant_code} not found", status_code=404)
        self._dispatch_mode.add(plant_code)
        return f"MODE_{plant_code}_{uuid.uuid4().hex[:8]}"

    def _require_dispatch_mode(self, plant_code: str) -> None:
        if plant_code not in self._dispatch_mode:
            raise HuaweiAuthError(
                f"Dispatch mode not enabled for plant {plant_code}. Call set_dispatch_mode() first."
            )

    # ------------------------------------------------------------------
    # Dispatch commands
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        plant_code: str,
        switch: DispatchSwitch,
        power_kw: float = 0.0,
        duration_min: int = 60,
        target_soc: float | None = None,
    ) -> HuaweiDispatchTask:
        """Issue a charge / discharge / stop command."""
        await asyncio.sleep(LATENCY_MS / 1000)
        self._require_dispatch_mode(plant_code)

        # Validate power
        batteries = [b for b in self._batteries.values() if b.plant_code == plant_code]
        if not batteries:
            raise HuaweiAPIError(f"No batteries for plant {plant_code}", status_code=404)

        total_max_kw = sum(b.max_power_kw for b in batteries)
        if power_kw > total_max_kw:
            raise HuaweiAPIError(
                f"Requested power {power_kw} kW exceeds plant capacity {total_max_kw} kW",
                status_code=422,
            )

        request_id = f"TASK_{uuid.uuid4().hex[:12]}"
        record = _TaskRecord(
            request_id=request_id,
            plant_code=plant_code,
            dispatch_switch=switch,
            power_kw=power_kw,
            duration_min=duration_min,
            target_soc=target_soc,
        )
        self._tasks[request_id] = record

        # Apply the command to batteries proportionally
        per_battery_kw = power_kw / len(batteries) if batteries else 0.0
        for bat in batteries:
            bat.dispatch_switch = switch
            if switch == DispatchSwitch.STOP:
                bat.current_power_kw = 0.0
                bat.dispatch_switch = DispatchSwitch.STOP
            elif switch == DispatchSwitch.CHARGE:
                bat.current_power_kw = min(per_battery_kw, bat.max_power_kw)
            elif switch == DispatchSwitch.DISCHARGE:
                bat.current_power_kw = -min(per_battery_kw, bat.max_power_kw)

        return HuaweiDispatchTask(
            request_id=request_id,
            plant_code=plant_code,
            dispatch_switch=switch,
            power_w=power_kw * 1000,
            duration_min=duration_min,
            target_soc=target_soc,
            status=TaskStatus.IN_PROGRESS,
        )

    async def stop_dispatch(self, plant_code: str) -> HuaweiDispatchTask:
        """Immediately stop all dispatch (emergency stop)."""
        await asyncio.sleep(LATENCY_MS / 1000)
        self._require_dispatch_mode(plant_code)

        for bat in self._batteries.values():
            if bat.plant_code == plant_code:
                bat.current_power_kw = 0.0
                bat.dispatch_switch = DispatchSwitch.STOP

        request_id = f"STOP_{uuid.uuid4().hex[:12]}"
        record = _TaskRecord(
            request_id=request_id,
            plant_code=plant_code,
            dispatch_switch=DispatchSwitch.STOP,
            power_kw=0.0,
            duration_min=0,
            target_soc=None,
            status=TaskStatus.COMPLETE,
        )
        self._tasks[request_id] = record
        return HuaweiDispatchTask(
            request_id=request_id,
            plant_code=plant_code,
            dispatch_switch=DispatchSwitch.STOP,
            status=TaskStatus.COMPLETE,
        )

    # ------------------------------------------------------------------
    # Task status polling
    # ------------------------------------------------------------------

    async def get_task_status(self, request_id: str) -> HuaweiDispatchTask:
        """Poll for the result of an async dispatch command."""
        await asyncio.sleep(LATENCY_MS / 1000)
        record = self._tasks.get(request_id)
        if record is None:
            raise HuaweiTaskError(f"Task {request_id} not found")

        # Auto-complete IN_PROGRESS tasks after a short delay (>50ms)
        if record.status == TaskStatus.IN_PROGRESS:
            elapsed_ms = (time.monotonic() - record.created_at) * 1000
            if elapsed_ms > 50:
                record.status = TaskStatus.COMPLETE

        return HuaweiDispatchTask(
            request_id=record.request_id,
            plant_code=record.plant_code,
            dispatch_switch=record.dispatch_switch,
            power_w=record.power_kw * 1000,
            duration_min=record.duration_min,
            target_soc=record.target_soc,
            status=record.status,
        )

    # ------------------------------------------------------------------
    # Introspection (test helpers)
    # ------------------------------------------------------------------

    def get_battery_state(self, device_id: str) -> _BatteryState | None:
        """Direct access to internal battery state for test assertions."""
        return self._batteries.get(device_id)

    def set_soc(self, plant_or_device_id: str, soc: float) -> None:
        """Directly set SoC for deterministic test setup. Accepts plant_code or device_id."""
        bat = self._batteries.get(plant_or_device_id)
        if bat is None:
            # Try looking up by plant_code (device_id is "DEV_{plant_code}")
            bat = self._batteries.get(f"DEV_{plant_or_device_id}")
        if bat:
            bat.soc = max(SOC_MIN, min(SOC_MAX, soc))

    # ------------------------------------------------------------------
    # High-level dispatch API (mirrors HuaweiClient public interface)
    # ------------------------------------------------------------------

    async def charge(self, plant_code: str, power_w: float) -> HuaweiDispatchTask:
        """Send a charge command. power_w must be > 0 (Watts)."""
        await asyncio.sleep(LATENCY_MS / 1000)
        if power_w <= 0:
            raise ValueError("power_w > 0 required for charge command")
        if plant_code not in self._dispatch_mode:
            raise HuaweiAPIError(
                f"thirdPartyDispatch not enabled for plant {plant_code}",
                fail_code=401,
            )
        if plant_code in self._pending_tasks:
            raise HuaweiTaskError(
                f"Plant {plant_code} already has a pending task {self._pending_tasks[plant_code]}"
            )

        batteries = [b for b in self._batteries.values() if b.plant_code == plant_code]
        if not batteries:
            raise HuaweiAPIError(f"No batteries for plant {plant_code}", fail_code=404)

        power_kw = power_w / 1000.0
        per_battery_kw = power_kw / len(batteries)
        for bat in batteries:
            clamped = min(per_battery_kw, bat.max_power_kw)
            bat.current_power_kw = clamped
            bat.dispatch_switch = DispatchSwitch.CHARGE

        request_id = f"TASK_{uuid.uuid4().hex[:12]}"
        record = _TaskRecord(
            request_id=request_id,
            plant_code=plant_code,
            dispatch_switch=DispatchSwitch.CHARGE,
            power_kw=per_battery_kw,
            duration_min=60,
            target_soc=None,
        )
        self._tasks[request_id] = record
        self._pending_tasks[plant_code] = request_id

        return HuaweiDispatchTask(
            request_id=request_id,
            plant_code=plant_code,
            dispatch_switch=DispatchSwitch.CHARGE,
            power_w=power_w,
            status=TaskStatus.IN_PROGRESS,
        )

    async def discharge(
        self, plant_code: str, power_w: float, target_soc: float | None = None
    ) -> HuaweiDispatchTask:
        """Send a discharge command. power_w must be > 0 (Watts)."""
        await asyncio.sleep(LATENCY_MS / 1000)
        if power_w <= 0:
            raise ValueError("power_w > 0 required for discharge command")
        if plant_code not in self._dispatch_mode:
            raise HuaweiAPIError(
                f"thirdPartyDispatch not enabled for plant {plant_code}",
                fail_code=401,
            )
        if plant_code in self._pending_tasks:
            raise HuaweiTaskError(
                f"Plant {plant_code} already has a pending task {self._pending_tasks[plant_code]}"
            )

        batteries = [b for b in self._batteries.values() if b.plant_code == plant_code]
        if not batteries:
            raise HuaweiAPIError(f"No batteries for plant {plant_code}", fail_code=404)

        power_kw = power_w / 1000.0
        per_battery_kw = power_kw / len(batteries)
        for bat in batteries:
            clamped = min(per_battery_kw, bat.max_power_kw)
            bat.current_power_kw = -clamped
            bat.dispatch_switch = DispatchSwitch.DISCHARGE

        request_id = f"TASK_{uuid.uuid4().hex[:12]}"
        record = _TaskRecord(
            request_id=request_id,
            plant_code=plant_code,
            dispatch_switch=DispatchSwitch.DISCHARGE,
            power_kw=per_battery_kw,
            duration_min=60,
            target_soc=target_soc,
        )
        self._tasks[request_id] = record
        self._pending_tasks[plant_code] = request_id

        return HuaweiDispatchTask(
            request_id=request_id,
            plant_code=plant_code,
            dispatch_switch=DispatchSwitch.DISCHARGE,
            power_w=power_w,
            target_soc=target_soc,
            status=TaskStatus.IN_PROGRESS,
        )

    async def stop(self, plant_code: str) -> HuaweiDispatchTask:
        """Emergency stop — halts all dispatch immediately, no dispatch mode required."""
        await asyncio.sleep(LATENCY_MS / 1000)
        for bat in self._batteries.values():
            if bat.plant_code == plant_code:
                bat.current_power_kw = 0.0
                bat.dispatch_switch = DispatchSwitch.STOP

        self._pending_tasks.pop(plant_code, None)

        request_id = f"STOP_{uuid.uuid4().hex[:12]}"
        record = _TaskRecord(
            request_id=request_id,
            plant_code=plant_code,
            dispatch_switch=DispatchSwitch.STOP,
            power_kw=0.0,
            duration_min=0,
            target_soc=None,
            status=TaskStatus.COMPLETE,
        )
        self._tasks[request_id] = record

        return HuaweiDispatchTask(
            request_id=request_id,
            plant_code=plant_code,
            dispatch_switch=DispatchSwitch.STOP,
            status=TaskStatus.COMPLETE,
        )

    async def get_battery_realtime(
        self, device_ids: list[str], *, plant_code: str
    ) -> list[HuaweiBatteryStatus]:
        """Return real-time KPIs. Enforces a per-plant rate limit (Huawei NBI: 5 min)."""
        await asyncio.sleep(LATENCY_MS / 1000)
        now = time.monotonic()
        last = self._last_realtime.get(plant_code)
        if last is not None and (now - last) < REALTIME_RATE_LIMIT_S:
            raise HuaweiAPIError(
                f"Real-time KPI rate limit exceeded for plant {plant_code} (5-min interval)",
                fail_code=407,
            )
        self._last_realtime[plant_code] = now

        return [self._batteries[did].to_status() for did in device_ids if did in self._batteries]

    async def wait_for_task(self, request_id: str, plant_code: str) -> HuaweiDispatchTask:
        """Wait for a dispatch task to complete. Immediately resolves in the simulator."""
        await asyncio.sleep(LATENCY_MS / 1000)
        record = self._tasks.get(request_id)
        if record is None:
            raise HuaweiTaskError(f"Task {request_id} not found")
        record.status = TaskStatus.COMPLETE
        self._pending_tasks.pop(plant_code, None)

        return HuaweiDispatchTask(
            request_id=record.request_id,
            plant_code=record.plant_code,
            dispatch_switch=record.dispatch_switch,
            power_w=record.power_kw * 1000,
            duration_min=record.duration_min,
            target_soc=record.target_soc,
            status=TaskStatus.COMPLETE,
        )

    def inject_fault(self, device_id: str) -> None:
        """Simulate a battery fault for error-handling tests."""
        bat = self._batteries.get(device_id)
        if bat:
            bat.current_power_kw = 0.0
            bat.dispatch_switch = DispatchSwitch.STOP
            bat.temperature_c = 65.0  # overtemperature
