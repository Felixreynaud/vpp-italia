"""Pydantic v2 schemas for request/response validation."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from data.models import (
    BatteryProtocol,
    BatteryState,
    DispatchSource,
    MarketName,
    MGPZone,
    OfferStatus,
    UserRole,
)

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
    # Field name uses trailing underscore to avoid clashing with SQLAlchemy
    # Base.metadata; the underlying DB column is still "metadata".
    metadata_: dict[str, Any] | None = Field(
        default=None,
        description="Connector-specific config (plant_code, credentials, …)",
    )

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
    metadata_: dict[str, Any] | None = Field(default=None)


class BatteryResponse(BatteryBase):
    model_config = ConfigDict(from_attributes=True)

    battery_id: UUID
    state: BatteryState
    is_active: bool
    created_at: datetime
    updated_at: datetime

    # Runtime values enriched from the latest BatteryReading (nullable when
    # the battery has never reported yet).
    soc_percent: float | None = None
    power_kw: float | None = None
    voltage_v: float | None = None
    current_a: float | None = None
    temperature_c: float | None = None
    last_seen: datetime | None = None


class BatteryListResponse(BaseModel):
    data: list[BatteryResponse]
    meta: dict[str, Any]


# ---------------------------------------------------------------------------
# Connector discovery & bulk import (Huawei FusionSolar etc.)
# ---------------------------------------------------------------------------


class HuaweiDiscoverRequest(BaseModel):
    """Credentials to query a Huawei FusionSolar endpoint (real or simulator)."""

    endpoint_url: str = Field(
        ...,
        description="Base URL e.g. http://127.0.0.1:9999 (simulator) or https://intl.fusionsolar.huawei.com",
    )
    client_id: str = Field(..., max_length=128)
    client_secret: str = Field(..., max_length=256)


class DiscoveredBattery(BaseModel):
    plant_code: str
    plant_name: str
    device_id: str
    model: str | None = None
    capacity_kwh: Decimal
    max_power_kw: Decimal


class HuaweiDiscoverResponse(BaseModel):
    data: list[DiscoveredBattery]
    meta: dict[str, Any]


class BulkImportItem(BaseModel):
    """One battery from a discovery response, augmented with VPP-side params."""

    asset_id: str = Field(..., max_length=64)
    site_id: UUID
    name: str = Field(..., max_length=128)
    plant_code: str
    device_id: str
    model: str | None = None
    capacity_kwh: Decimal
    max_power_kw: Decimal


class BulkImportRequest(BaseModel):
    endpoint_url: str
    client_id: str
    client_secret: str
    batteries: list[BulkImportItem]


class BulkImportResponse(BaseModel):
    imported: int
    skipped: int
    battery_ids: list[UUID]


# ---------------------------------------------------------------------------
# Dispatch schemas
# ---------------------------------------------------------------------------


class DispatchCommand(BaseModel):
    power_kw: Decimal = Field(
        ..., description="Target power in kW (positive=discharge, negative=charge)"
    )
    duration_minutes: int = Field(default=15, ge=1, le=60)
    reason: str | None = Field(default=None, max_length=256)


class ScheduleSlot(BaseModel):
    hour: int = Field(..., ge=0, le=23)
    power_kw: float


class DispatchApplyRequest(BaseModel):
    site_id: UUID
    schedule: list[ScheduleSlot] = Field(..., min_length=24, max_length=24)
    source: str = Field(default="manual", description="manual | optimizer | market_signal")


class DispatchApplyResult(BaseModel):
    success: bool
    message: str
    applied_at: datetime
    plans_saved: int
    batteries_targeted: int


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
    def delivery_date_str(cls, v: date) -> str:
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


# ---------------------------------------------------------------------------
# User schemas
# ---------------------------------------------------------------------------


class UserInvite(BaseModel):
    """Payload sent by an admin to invite a new user."""

    email: str = Field(..., max_length=255)
    full_name: str = Field(..., max_length=128)
    role: UserRole = UserRole.OPERATOR

    @field_validator("email")
    @classmethod
    def email_must_contain_at(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("invalid email address")
        return v


class UserUpdate(BaseModel):
    """Admin-side update (cannot change email or password here)."""

    full_name: str | None = Field(default=None, max_length=128)
    role: UserRole | None = None
    is_active: bool | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    email_verified_at: datetime | None
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class UserListResponse(BaseModel):
    data: list[UserResponse]
    meta: dict[str, Any]


class PasswordResetRequest(BaseModel):
    email: str = Field(..., max_length=255)


class PasswordResetConfirm(BaseModel):
    token: str = Field(..., min_length=20, max_length=200)
    new_password: str = Field(..., min_length=10, max_length=128)

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("password must contain at least one digit")
        return v


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=10, max_length=128)

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("password must contain at least one digit")
        return v


# ---------------------------------------------------------------------------
# MGP Day-Ahead price schemas
# ---------------------------------------------------------------------------


class MGPPriceItem(BaseModel):
    """A single MGP price slot."""

    model_config = ConfigDict(from_attributes=True)

    delivery_date: str
    hour: int = Field(..., ge=0, le=23)
    zone: MGPZone
    price_eur_mwh: Decimal


class MGPPriceResponse(BaseModel):
    """Response for GET /api/v1/markets/mgp/prices.

    `prices` is the 24-hour curve for the requested (zone, date), in
    chronological order. `meta` carries the count and possibly metadata
    about the source (fetched_at).
    """

    data: list[MGPPriceItem]
    meta: dict[str, Any]
