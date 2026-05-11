"""Pydantic v2 schemas for request/response validation."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from data.models import BatteryProtocol, BatteryState, DispatchSource, MarketName, OfferStatus

T = TypeVar("T")


class PaginatedMeta(BaseModel):
    count: int
    next_cursor: str | None = None


class PaginatedResponse(BaseModel, Generic[T]):
    data: list[T]
    meta: dict[str, Any]


# ---------------------------------------------------------------------------
# Battery schemas
# ---------------------------------------------------------------------------


class BatteryBase(BaseModel):
    asset_id: str = Field(..., max_length=64)
    site_id: UUID
    name: str = Field(..., max_length=128)
    protocol: BatteryProtocol
    host: str = Field(..., max_length=255)
    port: int = Field(..., ge=1, le=65535)
    capacity_kwh: Decimal = Field(..., gt=0, decimal_places=2)
    max_power_kw: Decimal = Field(..., gt=0, decimal_places=2)
    min_soc_percent: Decimal = Field(default=Decimal("10.0"), ge=0, le=100)
    max_soc_percent: Decimal = Field(default=Decimal("90.0"), ge=0, le=100)
    ramp_rate_kw_per_min: Decimal | None = Field(default=None, gt=0)

    @field_validator("max_soc_percent")
    @classmethod
    def max_soc_above_min(cls, v: Decimal, info: Any) -> Decimal:
        min_soc = info.data.get("min_soc_percent", Decimal("0"))
        if v <= min_soc:
            raise ValueError("max_soc_percent must be greater than min_soc_percent")
        return v


class BatteryCreate(BatteryBase):
    pass


class BatteryUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    is_active: bool | None = None
    min_soc_percent: Decimal | None = Field(default=None, ge=0, le=100)
    max_soc_percent: Decimal | None = Field(default=None, ge=0, le=100)
    ramp_rate_kw_per_min: Decimal | None = None


class BatteryResponse(BatteryBase):
    model_config = ConfigDict(from_attributes=True)

    battery_id: UUID
    state: BatteryState
    is_active: bool
    created_at: datetime
    updated_at: datetime


class BatteryListResponse(BaseModel):
    data: list[BatteryResponse]
    meta: dict[str, Any]


# ---------------------------------------------------------------------------
# Dispatch schemas
# ---------------------------------------------------------------------------


class DispatchCommand(BaseModel):
    power_kw: Decimal = Field(
        ..., description="Target power in kW (positive=discharge, negative=charge)"
    )
    duration_minutes: int = Field(default=15, ge=1, le=60)
    reason: str | None = Field(default=None, max_length=256)


class DispatchCommandResponse(BaseModel):
    command_id: str
    battery_id: UUID
    power_kw: Decimal


class DispatchPlanCreate(BaseModel):
    battery_id: UUID
    delivery_date: date
    quarter_hour: int = Field(..., ge=0, le=95)
    power_kw: Decimal = Field(..., description="Target setpoint kW")

    @field_validator("delivery_date")
    @classmethod
    def delivery_date_str(cls, v: date) -> str:  # type: ignore[override]
        return v.isoformat()


class DispatchPlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    plan_id: UUID
    battery_id: UUID
    delivery_date: str
    quarter_hour: int
    power_kw: Decimal
    source: DispatchSource
    created_at: datetime


class DispatchPlanListResponse(BaseModel):
    data: list[DispatchPlanResponse]
    meta: dict[str, Any]


# ---------------------------------------------------------------------------
# Market offer schemas
# ---------------------------------------------------------------------------


class MarketOfferCreate(BaseModel):
    market: MarketName
    delivery_date: date
    quarter_hour_start: int = Field(..., ge=0, le=95)
    quarter_hour_end: int = Field(..., ge=0, le=95)
    energy_mwh: Decimal | None = Field(default=None, gt=0)
    capacity_mw: Decimal | None = Field(default=None, gt=0)
    price_eur_mwh: Decimal = Field(..., ge=0)
    direction: str = Field(..., pattern="^(UP|DOWN|BOTH)$")

    @field_validator("quarter_hour_end")
    @classmethod
    def end_after_start(cls, v: int, info: Any) -> int:
        start = info.data.get("quarter_hour_start", 0)
        if v < start:
            raise ValueError("quarter_hour_end must be >= quarter_hour_start")
        return v


class MarketOfferResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    offer_id: UUID
    market: MarketName
    delivery_date: str
    quarter_hour_start: int
    quarter_hour_end: int
    energy_mwh: Decimal | None
    capacity_mw: Decimal | None
    price_eur_mwh: Decimal
    direction: str
    external_id: str | None
    status: OfferStatus
    submitted_at: datetime | None
    created_at: datetime


class MarketOfferListResponse(BaseModel):
    data: list[MarketOfferResponse]
    meta: dict[str, Any]
