"""Optimization API endpoints for VPP Italia."""

from __future__ import annotations

import base64
import csv
import io
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Body, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import CurrentUser, DbSession
from core.dispatch.models import BatterySpec, DailySchedule
from core.optimization.arbitrage import ArbitrageInput, ArbitrageOptimizer
from core.optimization.peak_shaving import PeakShavingInput, PeakShavingOptimizer
from core.optimization.scenarios import SCENARIOS, ScenarioType
from core.optimization.stochastic import StochasticInput, StochasticOptimizer
from data.models import Battery

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/optimize", tags=["optimization"])


# ---------------------------------------------------------------------------
# Pydantic request schemas — JSON body, frontend-friendly
# ---------------------------------------------------------------------------


class AutoconsommationPayload(BaseModel):
    site_id: UUID
    production_pv_kw: list[float] = Field(..., min_length=24, max_length=24)
    consommation_kw: list[float] = Field(..., min_length=24, max_length=24)
    prix_mgp: list[float] = Field(..., min_length=24, max_length=24)


class ArbitragePayload(BaseModel):
    site_id: UUID
    prix_mgp: list[float] = Field(..., min_length=24, max_length=24)
    mode: Literal["conservateur", "standard", "agressif"] = "standard"


class StochastiquePayload(BaseModel):
    site_id: UUID
    prix_mgp_base: list[float] = Field(..., min_length=24, max_length=24)
    incertitude_pct: float = Field(default=20.0, ge=0.0, le=100.0)
    n_scenarios: int = Field(default=20, ge=5, le=200)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_battery_specs(db: AsyncSession, site_id: UUID) -> list[BatterySpec]:
    result = await db.execute(
        select(Battery).where(Battery.site_id == site_id, Battery.is_active.is_(True))
    )
    batteries = result.scalars().all()
    if not batteries:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active batteries found for site {site_id}",
        )
    return [_battery_to_spec(b) for b in batteries]


def _battery_to_spec(b: Battery) -> BatterySpec:
    return BatterySpec(
        battery_id=str(b.battery_id),
        capacity_kwh=float(b.capacity_kwh),
        max_power_kw=float(b.max_power_kw),
        soc_min_pct=float(b.min_soc_percent),
        soc_max_pct=float(b.max_soc_percent),
        ramp_kw_per_min=float(b.ramp_rate_kw_per_min) if b.ramp_rate_kw_per_min else None,
    )


def _schedule_to_hourly_list(
    schedule_kw: list[float] | DailySchedule,
) -> list[dict[str, float | int]]:
    """Convert any of the optimisers' schedule shapes to the simple
    [{hour, power_kw}] list expected by the frontend.

    Convention: positive = discharge, negative = charge (matches DispatchCommand).
    """
    if isinstance(schedule_kw, list):
        return [{"hour": h, "power_kw": float(kw)} for h, kw in enumerate(schedule_kw)]

    # DailySchedule case — sum batteries per hour
    out: list[dict[str, float | int]] = []
    for h in range(24):
        hs = schedule_kw.hours.get(h)
        out.append({"hour": h, "power_kw": float(hs.total_power_kw) if hs else 0.0})
    return out


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/autoconsommation")
async def optimize_autoconsommation(
    payload: AutoconsommationPayload,
    db: DbSession,
    _user: CurrentUser,
) -> dict[str, Any]:
    """Run peak-shaving / self-consumption optimisation for a site."""
    batteries = await _fetch_battery_specs(db, payload.site_id)
    bat = batteries[0]
    inp = PeakShavingInput(
        production_pv_kw=payload.production_pv_kw,
        consommation_site_kw=payload.consommation_kw,
        prix_mgp=payload.prix_mgp,
        soc_initial_pct=50.0,
        capacite_kwh=bat.capacity_kwh,
        puissance_max_kw=bat.max_power_kw,
        soc_min_pct=bat.soc_min_pct,
        soc_max_pct=bat.soc_max_pct,
    )
    result = PeakShavingOptimizer().optimize(inp)

    logger.info(
        "optimize.autoconsommation",
        site_id=str(payload.site_id),
        taux_pct=result.taux_autoconsommation_pct,
        economie_eur=result.economie_estimee_eur,
    )
    return {
        "data": {
            "schedule": _schedule_to_hourly_list(result.schedule_kw),
            "revenus_estimes_eur": float(result.economie_estimee_eur),
            "taux_autoconsommation_pct": float(result.taux_autoconsommation_pct),
            "scenario": "autoconsommation",
        },
        "meta": {"site_id": str(payload.site_id), "batteries": len(batteries), **result.metadata},
    }


@router.post("/arbitrage")
async def optimize_arbitrage(
    payload: ArbitragePayload,
    db: DbSession,
    _user: CurrentUser,
) -> dict[str, Any]:
    """Run MGP arbitrage optimisation with CVaR/Sharpe risk metrics."""
    batteries = await _fetch_battery_specs(db, payload.site_id)
    inp = ArbitrageInput(prix_mgp=payload.prix_mgp, batteries=batteries, mode=payload.mode)
    result = ArbitrageOptimizer().optimize(inp)

    logger.info(
        "optimize.arbitrage",
        site_id=str(payload.site_id),
        mode=payload.mode,
        revenu_eur=result.revenu_estime_eur,
        sharpe=result.sharpe_ratio,
    )
    return {
        "data": {
            "schedule": _schedule_to_hourly_list(result.schedule),
            "revenus_estimes_eur": float(result.revenu_estime_eur),
            "sharpe_ratio": float(result.sharpe_ratio),
            "cvar": float(result.cvar_95_eur),
            "scenario": "arbitrage",
        },
        "meta": {"site_id": str(payload.site_id), "mode": payload.mode, **result.metadata},
    }


@router.post("/stochastique")
async def optimize_stochastique(
    payload: StochastiquePayload,
    db: DbSession,
    _user: CurrentUser,
) -> dict[str, Any]:
    """Run scenario-based robust optimisation."""
    batteries = await _fetch_battery_specs(db, payload.site_id)
    inp = StochasticInput(
        prix_mgp_base=payload.prix_mgp_base,
        batteries=batteries,
        n_scenarios=payload.n_scenarios,
        incertitude_pct=payload.incertitude_pct,
    )
    result = StochasticOptimizer().optimize(inp)

    logger.info(
        "optimize.stochastique",
        site_id=str(payload.site_id),
        n_scenarios=payload.n_scenarios,
        revenu_espere=result.revenu_espere_eur,
        risque_p95=result.risque_p95_eur,
    )
    return {
        "data": {
            "schedule": _schedule_to_hourly_list(result.schedule_robuste),
            "revenus_estimes_eur": float(result.revenu_espere_eur),
            "cvar": float(result.risque_p95_eur),
            "scenario": "stochastique",
        },
        "meta": {
            "site_id": str(payload.site_id),
            "n_scenarios": payload.n_scenarios,
            **result.metadata,
        },
    }


@router.post("/backtest")
async def backtest(
    site_id: UUID,
    date_debut: date,
    date_fin: date,
    scenario: ScenarioType,
    db: DbSession,
    _user: CurrentUser,
) -> dict[str, Any]:
    """Run a historical backtest over a date range using the given scenario."""
    if date_fin < date_debut:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date_fin must be >= date_debut",
        )

    batteries = await _fetch_battery_specs(db, site_id)
    scenario_def = SCENARIOS[scenario]

    report = _build_backtest_report(batteries, date_debut, date_fin, scenario_def.name)
    csv_b64 = _report_to_csv_b64(report["daily_results"])

    logger.info(
        "optimize.backtest",
        site_id=str(site_id),
        scenario=scenario.value,
        date_debut=str(date_debut),
        date_fin=str(date_fin),
    )
    return {
        "data": {"rapport": report, "csv": csv_b64},
        "meta": {
            "site_id": str(site_id),
            "scenario": scenario.value,
            "batteries": len(batteries),
        },
    }


@router.get("/scenarios")
async def list_scenarios() -> list[dict[str, Any]]:
    """Return the catalogue of all defined optimisation scenarios."""
    return [
        {
            "type": sc.type.value,
            "name": sc.name,
            "description": sc.description,
            "optimizer_class": sc.optimizer_class,
            "market": sc.market,
            "tags": sc.tags,
            "available": sc.available,
            "future": sc.future,
            "default_params": sc.default_params,
        }
        for sc in SCENARIOS.values()
    ]


def _build_backtest_report(
    batteries: list[BatterySpec],
    date_debut: date,
    date_fin: date,
    scenario_name: str,
) -> dict[str, Any]:
    return {
        "periode": f"{date_debut} -> {date_fin}",
        "scenario": scenario_name,
        "batteries": [b.battery_id for b in batteries],
        "total_revenue_eur": 0.0,
        "total_cost_eur": 0.0,
        "total_pnl_eur": 0.0,
        "avg_daily_pnl_eur": 0.0,
        "note": "Historical price data not yet available; re-run after MGP data ingestion.",
        "daily_results": [],
    }


def _report_to_csv_b64(daily_results: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    if daily_results:
        writer.writerow(daily_results[0].keys())
        for row in daily_results:
            writer.writerow(row.values())
    else:
        writer.writerow(["date", "pnl_eur", "revenue_eur", "cost_eur"])
    return base64.b64encode(buf.getvalue().encode()).decode()
