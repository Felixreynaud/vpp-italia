"""Pydantic v2 models for Huawei SmartPVMS NBI responses."""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class DispatchSwitch(IntEnum):
    STOP = 0
    CHARGE = 1
    DISCHARGE = 2


class TaskStatus(IntEnum):
    COMPLETE = 0
    IN_PROGRESS = 1
    TIMEOUT = 2


class BatteryDevType(IntEnum):
    BATTERY_UNIT = 39   # Individual battery string / module
    ESS_SYSTEM = 41     # Energy Storage System (plant-level)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class HuaweiToken(BaseModel):
    access_token: str
    expires_in: int = Field(default=3600, description="Lifetime in seconds")
    token_type: str = "Bearer"

    @field_validator("access_token")
    @classmethod
    def token_not_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("access_token must not be empty")
        return v


# ---------------------------------------------------------------------------
# Plant / Station
# ---------------------------------------------------------------------------


class HuaweiPlant(BaseModel):
    """A FusionSolar plant (station) entry from getStationList."""

    plant_code: str = Field(..., description="Unique plant identifier (stationCode)")
    plant_name: str = Field(..., description="Human-readable plant name")
    capacity_kwh: float = Field(..., ge=0, description="Installed ESS capacity in kWh")
    address: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    capacity_kw: float | None = Field(default=None, description="Rated power in kW")
    grid_connection_date: str | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "HuaweiPlant":
        return cls(
            plant_code=raw["stationCode"],
            plant_name=raw.get("stationName", ""),
            capacity_kwh=float(raw.get("designCapacity", 0)),
            address=raw.get("address"),
            longitude=raw.get("longitude"),
            latitude=raw.get("latitude"),
            capacity_kw=raw.get("capacity"),
            grid_connection_date=raw.get("gridConnectionDate"),
        )


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------


class HuaweiDevice(BaseModel):
    """A device entry from getDevList."""

    device_id: str = Field(..., description="Unique device identifier (devDn)")
    device_name: str
    device_type_id: int = Field(..., description="39=battery unit, 41=ESS system")
    plant_code: str
    model: str | None = None
    sn: str | None = Field(default=None, description="Serial number")
    software_version: str | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any], plant_code: str) -> "HuaweiDevice":
        return cls(
            device_id=str(raw["devDn"]),
            device_name=raw.get("devName", ""),
            device_type_id=int(raw.get("devTypeId", 39)),
            plant_code=plant_code,
            model=raw.get("invType") or raw.get("model"),
            sn=raw.get("esn"),
            software_version=raw.get("softwareVersion"),
        )


# ---------------------------------------------------------------------------
# Battery real-time status
# ---------------------------------------------------------------------------


class HuaweiBatteryStatus(BaseModel):
    """Real-time KPIs for a battery device (devTypeId=39 or 41).

    All electrical values use SI units as returned by the Huawei API.
    power_kw: positive = charging, negative = discharging.
    """

    device_id: str
    plant_code: str | None = None
    soc: float = Field(..., ge=0.0, le=100.0, description="State of Charge in percent")
    power_kw: float = Field(..., description="Active power kW (+charge / -discharge)")
    voltage_v: float | None = Field(default=None, description="Pack voltage in V")
    current_a: float | None = Field(default=None, description="Pack current in A")
    temperature_c: float | None = Field(default=None, description="Cell temperature in °C")
    soh: float | None = Field(default=None, ge=0, le=100, description="State of Health %")
    status: int | None = Field(default=None, description="Huawei device status code")
    # Derived
    is_charging: bool = False
    is_discharging: bool = False

    def model_post_init(self, __context: Any) -> None:
        object.__setattr__(self, "is_charging", self.power_kw > 0)
        object.__setattr__(self, "is_discharging", self.power_kw < 0)

    @classmethod
    def from_kpi(cls, device_id: str, kpi: dict[str, Any], plant_code: str | None = None) -> "HuaweiBatteryStatus":
        """Parse a single KPI dict from getDevRealKpi response."""
        # Huawei uses positive = discharge internally for some firmware versions;
        # we normalise to positive = charge at this boundary.
        raw_power = float(kpi.get("battery_power", kpi.get("mppt_power", 0)) or 0)
        # Huawei: charge_discharge_power — positive = charge, negative = discharge
        charge_discharge = float(kpi.get("charge_discharge_power", raw_power) or raw_power)

        return cls(
            device_id=device_id,
            plant_code=plant_code,
            soc=float(kpi.get("battery_soc", kpi.get("soc", 0)) or 0),
            power_kw=charge_discharge,
            voltage_v=kpi.get("battery_voltage") or kpi.get("vbat"),
            current_a=kpi.get("battery_current") or kpi.get("ibat"),
            temperature_c=kpi.get("battery_temperature") or kpi.get("temp"),
            soh=kpi.get("battery_soh"),
            status=kpi.get("run_state") or kpi.get("battery_status"),
        )


# ---------------------------------------------------------------------------
# Dispatch task
# ---------------------------------------------------------------------------


class HuaweiDispatchTask(BaseModel):
    """Result of an async charge/discharge command."""

    request_id: str = Field(..., description="Huawei async task identifier")
    plant_code: str
    dispatch_switch: DispatchSwitch
    power_w: float | None = None
    duration_min: int | None = None
    target_soc: float | None = None
    status: TaskStatus = TaskStatus.IN_PROGRESS
    error_code: int | None = None
    error_message: str | None = None

    @property
    def is_complete(self) -> bool:
        return self.status == TaskStatus.COMPLETE

    @property
    def is_timed_out(self) -> bool:
        return self.status == TaskStatus.TIMEOUT

    @classmethod
    def from_status_response(cls, request_id: str, plant_code: str, raw: dict[str, Any]) -> "HuaweiDispatchTask":
        return cls(
            request_id=request_id,
            plant_code=plant_code,
            dispatch_switch=DispatchSwitch(int(raw.get("dispatchSwitch", 0))),
            status=TaskStatus(int(raw.get("status", TaskStatus.IN_PROGRESS))),
            error_code=raw.get("errorCode"),
            error_message=raw.get("errorMsg"),
        )
