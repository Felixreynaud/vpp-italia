"""SQLAlchemy ORM models — TimescaleDB compatible."""

import uuid
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
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


class Battery(Base):
    __tablename__ = "batteries"

    battery_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id = Column(String(64), unique=True, nullable=False, comment="Terna UPCA asset code")
    site_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    protocol: Mapped[BatteryProtocol] = mapped_column(Enum(BatteryProtocol), nullable=False)
    host = Column(String(255), nullable=False, comment="IP or hostname for protocol connection")
    port = Column(Integer, nullable=False)
    capacity_kwh = Column(Numeric(10, 2), nullable=False)
    max_power_kw = Column(Numeric(10, 2), nullable=False)
    min_soc_percent = Column(Numeric(5, 2), nullable=False, default=10.0)
    max_soc_percent = Column(Numeric(5, 2), nullable=False, default=90.0)
    ramp_rate_kw_per_min = Column(Numeric(8, 2), nullable=True)
    state: Mapped[BatteryState] = mapped_column(
        Enum(BatteryState), nullable=False, default=BatteryState.OFFLINE
    )
    is_active = Column(Boolean, nullable=False, default=True)
    metadata_ = Column("metadata", JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    readings = relationship("BatteryReading", back_populates="battery", lazy="dynamic")
    dispatch_plans = relationship("DispatchPlan", back_populates="battery", lazy="dynamic")


class BatteryReading(Base):
    """TimescaleDB hypertable — partitioned by time (10s granularity)."""

    __tablename__ = "battery_readings"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    time = Column(DateTime(timezone=True), nullable=False, index=True)
    battery_id = Column(
        UUID(as_uuid=True), ForeignKey("batteries.battery_id"), nullable=False, index=True
    )
    soc_percent = Column(Numeric(5, 2), nullable=True)
    power_kw = Column(
        Numeric(10, 2), nullable=True, comment="Positive = discharge, negative = charge"
    )
    voltage_v = Column(Numeric(8, 2), nullable=True)
    current_a = Column(Numeric(8, 2), nullable=True)
    temperature_c = Column(Numeric(6, 2), nullable=True)
    state: Mapped[BatteryState | None] = mapped_column(Enum(BatteryState), nullable=True)
    raw = Column(JSON, nullable=True, comment="Full raw payload from connector")

    battery = relationship("Battery", back_populates="readings")


class DispatchPlan(Base):
    """96 QH (quarter-hours) per day per battery."""

    __tablename__ = "dispatch_plans"
    __table_args__ = (
        UniqueConstraint(
            "battery_id", "delivery_date", "quarter_hour", name="uq_dispatch_plan_slot"
        ),
    )

    plan_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    battery_id = Column(
        UUID(as_uuid=True), ForeignKey("batteries.battery_id"), nullable=False, index=True
    )
    delivery_date = Column(String(10), nullable=False, comment="YYYY-MM-DD in Europe/Rome")
    quarter_hour = Column(Integer, nullable=False, comment="0-95, representing QH of the day")
    power_kw = Column(
        Numeric(10, 2), nullable=False, comment="Target power setpoint (+ discharge, - charge)"
    )
    source: Mapped[DispatchSource] = mapped_column(
        Enum(DispatchSource), nullable=False, default=DispatchSource.OPTIMIZER
    )
    optimization_run_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    battery = relationship("Battery", back_populates="dispatch_plans")


class MarketOffer(Base):
    """Offers submitted to GME or Terna markets."""

    __tablename__ = "market_offers"

    offer_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market: Mapped[MarketName] = mapped_column(Enum(MarketName), nullable=False, index=True)
    delivery_date = Column(String(10), nullable=False, comment="YYYY-MM-DD in Europe/Rome")
    quarter_hour_start = Column(Integer, nullable=False, comment="First QH covered (0-95)")
    quarter_hour_end = Column(Integer, nullable=False, comment="Last QH covered (inclusive)")
    energy_mwh = Column(Numeric(10, 3), nullable=True)
    capacity_mw = Column(Numeric(10, 3), nullable=True)
    price_eur_mwh = Column(Numeric(10, 2), nullable=False)
    direction = Column(String(10), nullable=False, comment="UP | DOWN | BOTH")
    external_id = Column(
        String(128), nullable=True, comment="ID returned by GME/Terna after submission"
    )
    status: Mapped[OfferStatus] = mapped_column(
        Enum(OfferStatus), nullable=False, default=OfferStatus.DRAFT
    )
    response_payload = Column(JSON, nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(Base):
    """Platform user — admin or operator."""

    __tablename__ = "users"

    user_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    # NULL until the user accepts the invitation and sets their password
    password_hash = Column(String(255), nullable=True)
    full_name = Column(String(128), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole), nullable=False, default=UserRole.OPERATOR
    )
    is_active = Column(Boolean, nullable=False, default=False)
    email_verified_at = Column(DateTime(timezone=True), nullable=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    failed_login_attempts = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
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

    token_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    token_hash = Column(String(128), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    user_agent = Column(String(255), nullable=True)
    ip_address = Column(String(45), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="refresh_tokens")


class PasswordResetToken(Base):
    """Single-use token for invitation onboarding or password reset.

    The plaintext token is sent by email; only its sha256 hash is persisted.
    """

    __tablename__ = "password_reset_tokens"

    token_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    token_hash = Column(String(128), unique=True, nullable=False, index=True)
    purpose: Mapped[PasswordResetPurpose] = mapped_column(
        Enum(PasswordResetPurpose), nullable=False, default=PasswordResetPurpose.RESET
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="reset_tokens")
