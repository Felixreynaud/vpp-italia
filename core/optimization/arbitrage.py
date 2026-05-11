"""CVaR and Sharpe-enriched MGP arbitrage optimizer.

Wraps the existing DispatchOptimizer greedy heuristic with risk metrics
computed from Monte-Carlo price scenarios.  Does NOT modify the base optimizer.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

from core.dispatch.models import BatterySpec, DailySchedule
from core.dispatch.optimizer import DispatchOptimizer

# Perturbation ranges per risk mode (fraction of price)
_MODE_PERTURBATIONS: dict[str, list[float]] = {
    "conservateur": [0.15, 0.15, 0.15],
    "standard": [0.15, 0.30, 0.45],
    "agressif": [0.30, 0.45, 0.60],
}
_N_SCENARIOS_RISK = 100
_ANNUALISATION_FACTOR = math.sqrt(252)


@dataclass
class ArbitrageInput:
    """Inputs for an MGP arbitrage optimisation."""

    prix_mgp: list[float]  # €/MWh, 24 hourly values
    batteries: list[BatterySpec]
    mode: str = "standard"  # "conservateur" | "standard" | "agressif"
    soc_initial_pct: float = 50.0


@dataclass
class ArbitrageResult:
    """Outputs of an MGP arbitrage run with risk metrics."""

    schedule: DailySchedule
    revenu_estime_eur: float
    sharpe_ratio: float  # annualised Sharpe proxy
    cvar_95_eur: float  # expected loss in worst 5% scenarios
    metadata: dict[str, Any] = field(default_factory=dict)


class ArbitrageOptimizer:
    """MGP arbitrage enriched with CVaR and Sharpe metrics."""

    def __init__(self) -> None:
        self._base_optimizer = DispatchOptimizer()

    def optimize(self, inp: ArbitrageInput) -> ArbitrageResult:
        """Compute schedule + risk metrics for one trading day.

        Args:
            inp: ArbitrageInput with prices, batteries, and risk mode.

        Returns:
            ArbitrageResult with schedule, revenue, Sharpe, and CVaR.
        """
        batteries = _apply_initial_soc(inp.batteries, inp.soc_initial_pct)
        schedule = self._run_base(inp.prix_mgp, batteries)
        base_revenue = schedule.estimated_pnl_eur

        scenarios = _generate_scenarios(inp.prix_mgp, inp.mode, _N_SCENARIOS_RISK)
        sim_revenues = _simulate_revenues(scenarios, batteries, self._base_optimizer)

        sharpe = _compute_sharpe(sim_revenues)
        cvar = _compute_cvar_95(sim_revenues)

        return ArbitrageResult(
            schedule=schedule,
            revenu_estime_eur=round(base_revenue, 4),
            sharpe_ratio=round(sharpe, 4),
            cvar_95_eur=round(cvar, 4),
            metadata={
                "mode": inp.mode,
                "n_scenarios": _N_SCENARIOS_RISK,
                "mean_sim_revenue_eur": round(statistics.mean(sim_revenues), 2),
                "std_sim_revenue_eur": round(
                    statistics.stdev(sim_revenues) if len(sim_revenues) > 1 else 0.0, 2
                ),
            },
        )

    def replan_with_actuals(
        self,
        current_soc: float,
        elapsed_hours: int,
        actual_prices: list[float],
        batteries: list[BatterySpec],
    ) -> ArbitrageResult:
        """Rolling-horizon MPC: re-optimise remaining hours with actual prices."""
        remaining_hours = 24 - elapsed_hours
        if len(actual_prices) < remaining_hours:
            raise ValueError(
                f"Need {remaining_hours} prices for remaining horizon, got {len(actual_prices)}"
            )
        remaining_prices = actual_prices[:remaining_hours]
        updated = _apply_initial_soc(batteries, current_soc)
        inp = ArbitrageInput(
            prix_mgp=remaining_prices,
            batteries=updated,
            mode="standard",
            soc_initial_pct=current_soc,
        )
        return self.optimize(inp)

    def _run_base(self, prix: list[float], batteries: list[BatterySpec]) -> DailySchedule:
        prices_dict = {h: p for h, p in enumerate(prix)}
        return self._base_optimizer.optimize_day(prices_dict, batteries)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_initial_soc(batteries: list[BatterySpec], soc_pct: float) -> list[BatterySpec]:
    from dataclasses import replace

    return [replace(b, initial_soc_pct=soc_pct) for b in batteries]


def _generate_scenarios(
    base_prices: list[float],
    mode: str,
    n_scenarios: int,
) -> list[list[float]]:
    perturbations = _MODE_PERTURBATIONS.get(mode, _MODE_PERTURBATIONS["standard"])
    n_levels = len(perturbations)
    scenarios: list[list[float]] = []
    for i in range(n_scenarios):
        level = perturbations[i % n_levels]
        direction = [1.0, 0.0, -1.0][i % 3] if n_levels == 3 else 1.0
        offset = 1.0 + direction * level * ((i // 3 + 1) / (n_scenarios // 3 + 1))
        scenario = [max(0.0, p * offset) for p in base_prices]
        scenarios.append(scenario)
    return scenarios


def _simulate_revenues(
    scenarios: list[list[float]],
    batteries: list[BatterySpec],
    optimizer: DispatchOptimizer,
) -> list[float]:
    revenues: list[float] = []
    for scenario_prices in scenarios:
        prices_dict = {h: p for h, p in enumerate(scenario_prices)}
        schedule = optimizer.optimize_day(prices_dict, batteries)
        revenues.append(schedule.estimated_pnl_eur)
    return revenues


def _compute_sharpe(revenues: list[float]) -> float:
    if len(revenues) < 2:
        return 0.0
    mean_rev = statistics.mean(revenues)
    std_rev = statistics.stdev(revenues)
    if std_rev == 0.0:
        return 0.0
    return (mean_rev / std_rev) * _ANNUALISATION_FACTOR


def _compute_cvar_95(revenues: list[float]) -> float:
    if not revenues:
        return 0.0
    sorted_rev = sorted(revenues)
    cutoff_idx = max(1, int(len(sorted_rev) * 0.05))
    tail = sorted_rev[:cutoff_idx]
    mean_tail = statistics.mean(tail)
    return abs(min(0.0, mean_tail))
