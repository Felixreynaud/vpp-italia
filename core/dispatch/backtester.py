"""Backtester — simulate dispatch over historical MGP prices.

Uses real GME price history via the mercati-energetici library to evaluate
how the optimizer would have performed over a given period.

Output:
  - BacktestReport with summary statistics
  - CSV export: one row per day with P&L, cycles, efficiency
  - JSON export: full daily_results for Grafana / analysis
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import date, timedelta
from typing import Any

import structlog

from core.dispatch.models import BacktestReport, BatterySpec, DailySchedule
from core.dispatch.optimizer import DispatchOptimizer
from core.market.gme_client import GMEPriceClient

logger = structlog.get_logger(__name__)


class Backtester:
    """Simulate the dispatch strategy over historical data.

    Args:
        gme_client: Client used to fetch historical MGP prices.
        optimizer: Optimizer instance (uses defaults if not provided).
        zone: GME price zone for the simulation.
    """

    def __init__(
        self,
        gme_client: GMEPriceClient,
        optimizer: DispatchOptimizer | None = None,
        zone: str = "SUD",
    ) -> None:
        self._gme = gme_client
        self._optimizer = optimizer or DispatchOptimizer()
        self._zone = zone

    # ------------------------------------------------------------------
    # Main simulation
    # ------------------------------------------------------------------

    async def simulate(
        self,
        date_start: date,
        date_end: date,
        batteries: list[BatterySpec],
        concurrency: int = 4,
    ) -> BacktestReport:
        """Run the optimizer on each day in [date_start, date_end] inclusive.

        Args:
            date_start: First delivery date to simulate.
            date_end: Last delivery date to simulate (inclusive).
            batteries: Battery fleet specs — initial_soc_pct is reset each day.
            concurrency: How many day-simulations to run in parallel.

        Returns:
            BacktestReport with summary + per-day results.
        """
        if date_end < date_start:
            raise ValueError("date_end must be >= date_start")

        days = [date_start + timedelta(days=i) for i in range((date_end - date_start).days + 1)]
        logger.info(
            "backtester.started",
            days=len(days),
            batteries=len(batteries),
            zone=self._zone,
        )

        # Process days in batches for parallel price fetching
        daily_results: list[dict[str, Any]] = []
        sem = asyncio.Semaphore(concurrency)

        async def _simulate_day(d: date) -> dict[str, Any]:
            async with sem:
                return await self._simulate_day(d, batteries)

        results = await asyncio.gather(*[_simulate_day(d) for d in days], return_exceptions=True)

        for d, result in zip(days, results, strict=False):
            if isinstance(result, BaseException):
                logger.warning("backtester.day_failed", date=str(d), error=str(result))
                daily_results.append({"date": str(d), "error": str(result), "pnl_eur": 0.0})
            else:
                daily_results.append(result)

        return self._build_report(date_start, date_end, batteries, daily_results)

    async def _simulate_day(self, target: date, batteries: list[BatterySpec]) -> dict[str, Any]:
        prices = await self._gme.get_mgp_prices(target)
        if not prices:
            raise ValueError(f"No prices available for {target}")

        # Reset initial SoC to 50% each day (conservative: start neutral)
        day_batteries = [
            BatterySpec(
                battery_id=b.battery_id,
                capacity_kwh=b.capacity_kwh,
                max_power_kw=b.max_power_kw,
                soc_min_pct=b.soc_min_pct,
                soc_max_pct=b.soc_max_pct,
                initial_soc_pct=50.0,
                efficiency_roundtrip=b.efficiency_roundtrip,
                ramp_kw_per_min=b.ramp_kw_per_min,
            )
            for b in batteries
        ]

        schedule = self._optimizer.optimize_day(
            prices_24h=prices,
            batteries=day_batteries,
            delivery_date=target,
            zone=self._zone,
        )

        cycles = self._count_cycles(schedule, day_batteries)
        efficiency = self._compute_efficiency(schedule, day_batteries)

        return {
            "date": str(target),
            "pnl_eur": round(schedule.estimated_pnl_eur, 2),
            "revenue_eur": round(schedule.estimated_revenue_eur, 2),
            "cost_eur": round(schedule.estimated_cost_eur, 2),
            "cycles": round(cycles, 3),
            "efficiency": round(efficiency, 4),
            "avg_price_eur_mwh": round(sum(prices.values()) / len(prices), 2) if prices else 0.0,
            "peak_hours": sum(
                1
                for hs in schedule.hours.values()
                if any(a.power_kw < 0 for a in hs.actions.values())
            ),
            "offpeak_hours": sum(
                1
                for hs in schedule.hours.values()
                if any(a.power_kw > 0 for a in hs.actions.values())
            ),
        }

    # ------------------------------------------------------------------
    # Report builder
    # ------------------------------------------------------------------

    def _build_report(
        self,
        date_start: date,
        date_end: date,
        batteries: list[BatterySpec],
        daily_results: list[dict[str, Any]],
    ) -> BacktestReport:
        valid = [r for r in daily_results if "error" not in r]
        if not valid:
            return BacktestReport(
                date_start=date_start,
                date_end=date_end,
                zone=self._zone,
                battery_ids=[b.battery_id for b in batteries],
                total_revenue_eur=0.0,
                total_cost_eur=0.0,
                total_pnl_eur=0.0,
                total_cycles=0.0,
                avg_daily_pnl_eur=0.0,
                best_day=None,
                best_day_pnl_eur=0.0,
                worst_day=None,
                worst_day_pnl_eur=0.0,
                avg_roundtrip_efficiency=0.0,
                daily_results=daily_results,
            )

        total_revenue = sum(r["revenue_eur"] for r in valid)
        total_cost = sum(r["cost_eur"] for r in valid)
        total_pnl = sum(r["pnl_eur"] for r in valid)
        total_cycles = sum(r["cycles"] for r in valid)
        avg_pnl = total_pnl / len(valid)
        avg_efficiency = sum(r["efficiency"] for r in valid) / len(valid) if valid else 0.0

        best = max(valid, key=lambda r: r["pnl_eur"])
        worst = min(valid, key=lambda r: r["pnl_eur"])

        report = BacktestReport(
            date_start=date_start,
            date_end=date_end,
            zone=self._zone,
            battery_ids=[b.battery_id for b in batteries],
            total_revenue_eur=round(total_revenue, 2),
            total_cost_eur=round(total_cost, 2),
            total_pnl_eur=round(total_pnl, 2),
            total_cycles=round(total_cycles, 1),
            avg_daily_pnl_eur=round(avg_pnl, 2),
            best_day=date.fromisoformat(best["date"]),
            best_day_pnl_eur=round(best["pnl_eur"], 2),
            worst_day=date.fromisoformat(worst["date"]),
            worst_day_pnl_eur=round(worst["pnl_eur"], 2),
            avg_roundtrip_efficiency=round(avg_efficiency, 4),
            daily_results=daily_results,
        )

        logger.info(
            "backtester.completed",
            days=len(valid),
            total_pnl=round(total_pnl, 2),
            avg_daily_pnl=round(avg_pnl, 2),
        )
        return report

    # ------------------------------------------------------------------
    # Physical metrics
    # ------------------------------------------------------------------

    @staticmethod
    def _count_cycles(schedule: DailySchedule, batteries: list[BatterySpec]) -> float:
        """Count full equivalent discharge cycles (DoD 80%) across all batteries."""
        total_cycles = 0.0
        for battery in batteries:
            discharged_kwh = sum(
                abs(a.power_kw) * 1.0
                for hs in schedule.hours.values()
                for bid, a in hs.actions.items()
                if bid == battery.battery_id and a.power_kw < 0
            )
            usable_kwh = battery.capacity_kwh * 0.80
            total_cycles += discharged_kwh / usable_kwh if usable_kwh > 0 else 0.0
        return total_cycles

    @staticmethod
    def _compute_efficiency(schedule: DailySchedule, batteries: list[BatterySpec]) -> float:
        """Compute actual roundtrip efficiency from charge/discharge energy ratio."""
        charge_kwh = sum(
            a.power_kw * 1.0
            for hs in schedule.hours.values()
            for a in hs.actions.values()
            if a.power_kw > 0
        )
        discharge_kwh = sum(
            abs(a.power_kw) * 1.0
            for hs in schedule.hours.values()
            for a in hs.actions.values()
            if a.power_kw < 0
        )
        if charge_kwh == 0:
            return 0.0
        return min(discharge_kwh / charge_kwh, 1.0)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def to_csv(self, report: BacktestReport) -> str:
        """Export daily results to CSV string."""
        if not report.daily_results:
            return ""
        fields = list(report.daily_results[0].keys())
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report.daily_results)
        return buf.getvalue()

    def to_json(self, report: BacktestReport) -> str:
        """Export full report to JSON string."""
        data = report.to_summary()
        data["daily_results"] = report.daily_results
        return json.dumps(data, indent=2, default=str)
