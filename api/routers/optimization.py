"""Optimization API endpoints for VPP Italia."""

from __future__ import annotations

import base64
import csv
import io
from datetime import date
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import CurrentUser, DbSession, get_current_user, get_db
from core.dispatch.models import BatterySpec
from core.optimization.arbitrage import ArbitrageInput, ArbitrageOptimizer
from core.optimization.peak_shaving import PeakShavingInput, PeakShavingOptimizer
from core.optimization.scenarios import SCENARIOS, ScenarioType
from core.optimization.stochastic import StochasticInput, StochasticOptimizer
from data.models import Battery

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/optimize", tags=["optimization"])


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


def _validate_24h(values: list[float], field_name: str) -> None:
    if len(values) != 24:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field_name} must have exactly 24 values, got {len(values)}",
        )


@router.post("/autoconsommation")
async def optimize_autoconsommation(
    site_id: UUID,
    production_pv_kw: list[float],
    consommation_kw: list[float],
    prix_mgp: list[float],
    db: DbSession,
    _user: CurrentUser,
) -> dict[str, Any]:
    """Run peak-shaving / self-consumption optimisation for a site."""
    _validate_24h(production_pv_kw, "production_pv_kw")
    _validate_24h(consommation_kw, "consommation_kw")
    _validate_24h(prix_mgp, "prix_mgp")

    batteries = await _fetch_battery_specs(db, site_id)
    bat = batteries[0]
    inp = PeakShavingInput(
        production_pv_kw=production_pv_kw,
        consommation_site_kw=consommation_kw,
        prix_mgp=prix_mgp,
        soc_initial_pct=bat.initial_soc_pct,
        capacite_kwh=bat.capacity_kwh,
        puissance_max_kw=bat.max_power_kw,
        soc_min_pct=bat.soc_min_pct,
        soc_max_pct=bat.soc_max_pct,
    )
    result = PeakShavingOptimizer().optimize(inp)

    logger.info(
        "optimize.autoconsommation",
        site_id=str(site_id),
        taux_pct=result.taux_autoconsommation_pct,
        economie_eur=result.economie_estimee_eur,
    )
    return {
        "data": {
            "schedule": result.schedule_kw,
            "soc_evolution_pct": result.soc_evolution_pct,
            "surplus_pv_kw": result.surplus_pv_kw,
            "achat_reseau_kw": result.achat_reseau_kw,
            "taux_autoconsommation": result.taux_autoconsommation_pct,
            "economie_eur": result.economie_estimee_eur,
        },
        "meta": {"site_id": str(site_id), "batteries": len(batteries), **result.metadata},
    }


@router.post("/arbitrage")
async def optimize_arbitrage(
    site_id: UUID,
    prix_mgp: list[float],
    db: DbSession,
    _user: CurrentUser,
    mode: Annotated[str, Query()] = "standard",
) -> dict[str, Any]:
    """Run MGP arbitrage optimisation with CVaR/Sharpe risk metrics."""
    _validate_24h(prix_mgp, "prix_mgp")
    if mode not in ("conservateur", "standard", "agressif"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="mode must be one of: conservateur, standard, agressif",
        )

    batteries = await _fetch_battery_specs(db, site_id)
    inp = ArbitrageInput(prix_mgp=prix_mgp, batteries=batteries, mode=mode)
    result = ArbitrageOptimizer().optimize(inp)

    logger.info(
        "optimize.arbitrage",
        site_id=str(site_id),
        mode=mode,
        revenu_eur=result.revenu_estime_eur,
        sharpe=result.sharpe_ratio,
    )
    return {
        "data": {
            "schedule": result.schedule.to_dict(),
            "revenu_estime_eur": result.revenu_estime_eur,
            "sharpe_ratio": result.sharpe_ratio,
            "cvar": result.cvar_95_eur,
        },
        "meta": {"site_id": str(site_id), "mode": mode, **result.metadata},
    }


@router.post("/stochastique")
async def optimize_stochastique(
    site_id: UUID,
    prix_mgp_base: list[float],
    db: DbSession,
    _user: CurrentUser,
    incertitude_pct: Annotated[float, Query(ge=0.0, le=100.0)] = 20.0,
    n_scenarios: Annotated[int, Query(ge=5, le=200)] = 20,
) -> dict[str, Any]:
    """Run scenario-based robust optimisation."""
    _validate_24h(prix_mgp_base, "prix_mgp_base")

    batteries = await _fetch_battery_specs(db, site_id)
    inp = StochasticInput(
        prix_mgp_base=prix_mgp_base,
        batteries=batteries,
        n_scenarios=n_scenarios,
        incertitude_pct=incertitude_pct,
    )
    result = StochasticOptimizer().optimize(inp)

    logger.info(
        "optimize.stochastique",
        site_id=str(site_id),
        n_scenarios=n_scenarios,
        revenu_espere=result.revenu_espere_eur,
        risque_p95=result.risque_p95_eur,
    )
    return {
        "data": {
            "schedule_robuste": result.schedule_robuste.to_dict(),
            "revenu_espere_eur": result.revenu_espere_eur,
            "risque_p95_eur": result.risque_p95_eur,
            "scenarios_revenus": result.scenarios_revenus,
        },
        "meta": {"site_id": str(site_id), **result.metadata},
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
