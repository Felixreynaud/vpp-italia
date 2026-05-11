"""Domain models for the dispatch engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class ActionType(StrEnum):
    CHARGE = "charge"
    DISCHARGE = "discharge"
    STOP = "stop"


class HourType(StrEnum):
    PEAK = "peak"  # prix > moyenne + threshold * std
    OFF_PEAK = "off_peak"  # prix < moyenne - threshold * std
    NEUTRAL = "neutral"


class ScheduleStatus(StrEnum):
    PLANNED = "planned"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    OVERRIDDEN = "overridden"


@dataclass
class HourlyPrice:
    """Single hourly price entry from GME."""

    hour: int  # 0–23 (CET/CEST)
    price_eur_mwh: float
    zone: str  # NORD | CNORD | CSUD | SUD | SICI | SARD
    market: str  # MGP | MI-A1 … MI-A7
    date: date | None = None
    hour_type: HourType = HourType.NEUTRAL


@dataclass
class BatterySpec:
    """Physical characteristics of a battery asset used by the optimizer."""

    battery_id: str
    capacity_kwh: float
    max_power_kw: float
    soc_min_pct: float = 10.0
    soc_max_pct: float = 90.0
    initial_soc_pct: float = 50.0
    efficiency_roundtrip: float = 0.92
    ramp_kw_per_min: float | None = None
    min_cycle_minutes: int = 30


@dataclass
class DispatchAction:
    """A single dispatch command for one battery at one hour."""

    battery_id: str
    hour: int
    action_type: ActionType
    power_kw: float
    target_price_eur_mwh: float
    estimated_revenue_eur: float
    hour_type: HourType = HourType.NEUTRAL
    soc_before_pct: float = 0.0
    soc_after_pct: float = 0.0


@dataclass
class HourlySchedule:
    """All battery actions for a given hour."""

    hour: int
    actions: dict[str, DispatchAction] = field(default_factory=dict)  # battery_id → action
    total_power_kw: float = 0.0
    hour_price_eur_mwh: float = 0.0


@dataclass
class DailySchedule:
    """Full 24-hour dispatch plan for all batteries."""

    date: date
    zone: str
    hours: dict[int, HourlySchedule] = field(default_factory=dict)  # hour → HourlySchedule
    estimated_revenue_eur: float = 0.0
    estimated_cost_eur: float = 0.0
    status: ScheduleStatus = ScheduleStatus.PLANNED
    optimization_run_id: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def estimated_pnl_eur(self) -> float:
        return self.estimated_revenue_eur - self.estimated_cost_eur

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": str(self.date),
            "zone": self.zone,
            "estimated_revenue_eur": round(self.estimated_revenue_eur, 2),
            "estimated_cost_eur": round(self.estimated_cost_eur, 2),
            "estimated_pnl_eur": round(self.estimated_pnl_eur, 2),
            "status": self.status.value,
            "hours": {
                str(h): {
                    "price_eur_mwh": round(hs.hour_price_eur_mwh, 2),
                    "total_power_kw": round(hs.total_power_kw, 2),
                    "batteries": {
                        bid: {
                            "action": a.action_type.value,
                            "power_kw": round(a.power_kw, 2),
                            "soc_before": round(a.soc_before_pct, 1),
                            "soc_after": round(a.soc_after_pct, 1),
                            "revenue_eur": round(a.estimated_revenue_eur, 4),
                        }
                        for bid, a in hs.actions.items()
                    },
                }
                for h, hs in sorted(self.hours.items())
            },
        }


@dataclass
class DispatchLog:
    """Audit record comparing planned vs. actual execution."""

    timestamp: datetime
    battery_id: str
    hour: int
    planned_action: ActionType
    planned_power_kw: float
    actual_power_kw: float
    planned_soc_pct: float
    actual_soc_pct: float
    soc_deviation_pct: float
    price_eur_mwh: float
    revenue_eur: float
    success: bool
    error: str | None = None


@dataclass
class BacktestReport:
    """Results of a backtest simulation over a date range."""

    date_start: date
    date_end: date
    zone: str
    battery_ids: list[str]
    total_revenue_eur: float
    total_cost_eur: float
    total_pnl_eur: float
    total_cycles: float  # Full equivalent cycles (DoD 80%)
    avg_daily_pnl_eur: float
    best_day: date | None
    best_day_pnl_eur: float
    worst_day: date | None
    worst_day_pnl_eur: float
    avg_roundtrip_efficiency: float
    daily_results: list[dict[str, Any]] = field(default_factory=list)

    def to_summary(self) -> dict[str, Any]:
        return {
            "period": f"{self.date_start} → {self.date_end}",
            "zone": self.zone,
            "batteries": self.battery_ids,
            "total_revenue_eur": round(self.total_revenue_eur, 2),
            "total_cost_eur": round(self.total_cost_eur, 2),
            "total_pnl_eur": round(self.total_pnl_eur, 2),
            "avg_daily_pnl_eur": round(self.avg_daily_pnl_eur, 2),
            "total_cycles": round(self.total_cycles, 1),
            "best_day": str(self.best_day) if self.best_day else None,
            "best_day_pnl_eur": round(self.best_day_pnl_eur, 2),
            "worst_day": str(self.worst_day) if self.worst_day else None,
            "worst_day_pnl_eur": round(self.worst_day_pnl_eur, 2),
            "avg_roundtrip_efficiency_pct": round(self.avg_roundtrip_efficiency * 100, 1),
        }
