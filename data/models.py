"""SQLAlchemy ORM models — TimescaleDB compatible."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class BatteryProtocol(StrEnum):
    MODBUS = "modbus"
    OCPP = "ocpp"
    REST = "rest"


class BatteryState(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    SAFE_STATE = "safe_state"
    CHARGING = "charging"
    DISCHARGING = "discharging"
    IDLE = "idle"
    FAULT = "fault"


class DispatchSource(StrEnum):
    OPTIMIZER = "optimizer"
    MANUAL = "manual"
    MARKET_SIGNAL = "market_signal"


class MarketName(StrEnum):
    MGP = "MGP"
    MI = "MI"
    MSD = "MSD"
    MSD_GME = "MSD_GME"
    MB = "MB"


class OfferStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class UserRole(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"


class PasswordResetPurpose(StrEnum):
    INVITE = "invite"
    RESET = "reset"


class MGPZone(StrEnum):
    """Italian Day-Ahead market zones published by GME.

    PUN is the national weighted average price (not a zone per se, but
    treated as one for storage convenience).
    """

    NORD = "NORD"
    CNOR = "CNOR"
    CSUD = "CSUD"
    SUD = "SUD"
    CALA = "CALA"
    SARD = "SARD"
    SICI = "SICI"
    PUN = "PUN"


class AggregateStrategy(StrEnum):
    """Optimization strategies that can be applied to an aggregate.

    Only `ARBITRAGE_MGP` is wired to the actual optimizer for now.
    Other values are reserved for future iterations.
    """

    ARBITRAGE_MGP = "arbitrage_mgp"
    AUTOCONSOMMATION = "autoconsommation"
    MSD = "msd"
    STOCHASTIQUE = "stochastique"


class Battery(Base):
    __tablename__ = "batteries"

    battery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_id: Mapped[str] = mapped_column(String(64), unique=True, comment="Terna UPCA asset code")
    site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    name: Mapped[str] = mapped_column(String(128))
    protocol: Mapped[BatteryProtocol] = mapped_column(Enum(BatteryProtocol))
    host: Mapped[str] = mapped_column(String(255), comment="IP or hostname for protocol connection")
    port: Mapped[int] = mapped_column(Integer)
    capacity_kwh: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    max_power_kw: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    min_soc_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("10.0"))
    max_soc_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("90.0"))
    ramp_rate_kw_per_min: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    state: Mapped[BatteryState] = mapped_column(Enum(BatteryState), default=BatteryState.OFFLINE)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Optional grouping. NULL means "under management but not part of any
    # aggregate" — battery is still controllable individually.
    aggregate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("aggregates.aggregate_id", ondelete="SET NULL"),
        index=True,
    )
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    readings = relationship("BatteryReading", back_populates="battery", lazy="dynamic")
    dispatch_plans = relationship("DispatchPlan", back_populates="battery", lazy="dynamic")
    aggregate = relationship("Aggregate", back_populates="batteries")


class BatteryReading(Base):
    """TimescaleDB hypertable — partitioned by time (10s granularity)."""

    __tablename__ = "battery_readings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    battery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("batteries.battery_id"), index=True
    )
    soc_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    power_kw: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), comment="Positive = discharge, negative = charge"
    )
    voltage_v: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    current_a: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    temperature_c: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    state: Mapped[BatteryState | None] = mapped_column(Enum(BatteryState))
    raw: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, comment="Full raw payload from connector"
    )

    battery = relationship("Battery", back_populates="readings")


class DispatchPlan(Base):
    """96 QH (quarter-hours) per day per battery."""

    __tablename__ = "dispatch_plans"
    __table_args__ = (
        UniqueConstraint(
            "battery_id", "delivery_date", "quarter_hour", name="uq_dispatch_plan_slot"
        ),
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    battery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("batteries.battery_id"), index=True
    )
    delivery_date: Mapped[str] = mapped_column(String(10), comment="YYYY-MM-DD in Europe/Rome")
    quarter_hour: Mapped[int] = mapped_column(Integer, comment="0-95, representing QH of the day")
    power_kw: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), comment="Target power setpoint (+ discharge, - charge)"
    )
    source: Mapped[DispatchSource] = mapped_column(
        Enum(DispatchSource), default=DispatchSource.OPTIMIZER
    )
    optimization_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    battery = relationship("Battery", back_populates="dispatch_plans")


class MarketOffer(Base):
    """Offers submitted to GME or Terna markets."""

    __tablename__ = "market_offers"

    offer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    market: Mapped[MarketName] = mapped_column(Enum(MarketName), index=True)
    delivery_date: Mapped[str] = mapped_column(String(10), comment="YYYY-MM-DD in Europe/Rome")
    quarter_hour_start: Mapped[int] = mapped_column(Integer, comment="First QH covered (0-95)")
    quarter_hour_end: Mapped[int] = mapped_column(Integer, comment="Last QH covered (inclusive)")
    energy_mwh: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    capacity_mw: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    price_eur_mwh: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    direction: Mapped[str] = mapped_column(String(10), comment="UP | DOWN | BOTH")
    external_id: Mapped[str | None] = mapped_column(
        String(128), comment="ID returned by GME/Terna after submission"
    )
    status: Mapped[OfferStatus] = mapped_column(Enum(OfferStatus), default=OfferStatus.DRAFT)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(Base):
    """Platform user — admin or operator."""

    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    # NULL until the user accepts the invitation and sets their password
    password_hash: Mapped[str | None] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.OPERATOR)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    reset_tokens = relationship(
        "PasswordResetToken", back_populates="user", cascade="all, delete-orphan"
    )
    refresh_tokens = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )


class RefreshToken(Base):
    """Long-lived refresh token (7 days) stored hashed, revocable.

    The plaintext token is delivered to the client in a httpOnly cookie;
    only its sha256 hash is persisted, so DB compromise does not yield
    usable tokens.
    """

    __tablename__ = "refresh_tokens"

    token_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="refresh_tokens")


class PasswordResetToken(Base):
    """Single-use token for invitation onboarding or password reset.

    The plaintext token is sent by email; only its sha256 hash is persisted.
    """

    __tablename__ = "password_reset_tokens"

    token_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    purpose: Mapped[PasswordResetPurpose] = mapped_column(
        Enum(PasswordResetPurpose), default=PasswordResetPurpose.RESET
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="reset_tokens")


class MGPPrice(Base):
    """Italian Day-Ahead spot price published by GME (Gestore dei Mercati Energetici).

    One row per (delivery_date, hour, zone). Published every day at ~12:00
    Europe/Rome for the next delivery day. 8 zones (7 + PUN) × 24 hours =
    192 rows per fetch. Unique constraint enforces idempotency of inserts.
    """

    __tablename__ = "mgp_prices"
    __table_args__ = (UniqueConstraint("delivery_date", "hour", "zone", name="uq_mgp_prices_slot"),)

    # Integer (not BigInteger) for portable autoincrement: SQLite only auto-
    # increments INTEGER PRIMARY KEY. 70k rows/year × 30 years << 2 billion.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    delivery_date: Mapped[str] = mapped_column(
        String(10), index=True, comment="YYYY-MM-DD in Europe/Rome"
    )
    hour: Mapped[int] = mapped_column(Integer, comment="0-23, Europe/Rome local hour")
    zone: Mapped[MGPZone] = mapped_column(Enum(MGPZone), index=True)
    price_eur_mwh: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Aggregate(Base):
    """Group of batteries sharing one optimization strategy.

    Exclusive membership: each battery belongs to at most one aggregate
    at a time (enforced by the FK column on Battery, no join table).
    """

    __tablename__ = "aggregates"

    aggregate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str | None] = mapped_column(String(512))
    strategy_type: Mapped[AggregateStrategy] = mapped_column(
        Enum(AggregateStrategy), default=AggregateStrategy.ARBITRAGE_MGP
    )
    # Optional market target (MGP/MI/MSD/MB) — null = generic, configured later.
    target_market: Mapped[MarketName | None] = mapped_column(Enum(MarketName))
    target_zone: Mapped[MGPZone | None] = mapped_column(Enum(MGPZone))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    batteries = relationship("Battery", back_populates="aggregate", lazy="selectin")
