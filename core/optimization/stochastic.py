"""Scenario-based robust optimisation for VPP Italia.

Generates N price scenarios, runs ArbitrageOptimizer on each,
and selects the schedule that maximises E[revenue] - lambda * CVaR.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field
from typing import Any

from core.dispatch.models import BatterySpec, DailySchedule
from core.optimization.arbitrage import ArbitrageInput, ArbitrageOptimizer

_LAMBDA = 0.5  # risk-aversion parameter in E[rev] - lambda * CVaR objective


@dataclass
class StochasticInput:
    """Inputs for scenario-based robust optimisation."""

    prix_mgp_base: list[float]          # €/MWh, 24 hourly base prices
    batteries: list[BatterySpec]
    n_scenarios: int = 20
    incertitude_pct: float = 20.0       # +/-% uniform noise around base price
    random_seed: int | None = None


@dataclass
class StochasticResult:
    """Outputs of scenario-based optimisation."""

    schedule_robuste: DailySchedule     # best robust schedule
    revenu_espere_eur: float
    risque_p95_eur: float               # 95th-pct loss vs expected
    scenarios_revenus: list[float]      # per-scenario revenues
    metadata: dict = field(default_factory=dict)


class StochasticOptimizer:
    """Robust optimiser using scenario sampling and CVaR objective."""

    def __init__(self) -> None:
        self._arb = ArbitrageOptimizer()

    def optimize(self, inp: StochasticInput) -> StochasticResult:
        """Run scenario-based robust optimisation.

        Args:
            inp: StochasticInput with base prices, fleet, and uncertainty level.

        Returns:
            StochasticResult with robust schedule and risk statistics.
        """
        rng = random.Random(inp.random_seed)
        scenarios = _generate_scenarios(inp.prix_mgp_base, inp.incertitude_pct, inp.n_scenarios, rng)

        schedules, revenues = _evaluate_scenarios(scenarios, inp.batteries, self._arb)

        best_idx = _select_robust_schedule(revenues)
        best_schedule = schedules[best_idx]

        revenu_espere = statistics.mean(revenues) if revenues else 0.0
        risque_p95 = _compute_risque_p95(revenues, revenu_espere)

        return StochasticResult(
            schedule_robuste=best_schedule,
            revenu_espere_eur=round(revenu_espere, 4),
            risque_p95_eur=round(risque_p95, 4),
            scenarios_revenus=[round(r, 4) for r in revenues],
            metadata={
                "n_scenarios": inp.n_scenarios,
                "incertitude_pct": inp.incertitude_pct,
                "lambda": _LAMBDA,
                "best_scenario_idx": best_idx,
                "min_revenue_eur": round(min(revenues), 2) if revenues else 0.0,
                "max_revenue_eur": round(max(revenues), 2) if revenues else 0.0,
            },
        )


def _generate_scenarios(
    base_prices: list[float],
    incertitude_pct: float,
    n_scenarios: int,
    rng: random.Random,
) -> list[list[float]]:
    bound = incertitude_pct / 100.0
    scenarios: list[list[float]] = []
    for _ in range(n_scenarios):
        scenario = [
            max(0.0, p * (1.0 + rng.uniform(-bound, bound)))
            for p in base_prices
        ]
        scenarios.append(scenario)
    return scenarios


def _evaluate_scenarios(
    scenarios: list[list[float]],
    batteries: list[BatterySpec],
    arb: ArbitrageOptimizer,
) -> tuple[list[DailySchedule], list[float]]:
    schedules: list[DailySchedule] = []
    revenues: list[float] = []
    for prices in scenarios:
        inp = ArbitrageInput(prix_mgp=prices, batteries=batteries, mode="standard")
        result = arb.optimize(inp)
        schedules.append(result.schedule)
        revenues.append(result.revenu_estime_eur)
    return schedules, revenues


def _compute_cvar(revenues: list[float], alpha: float = 0.05) -> float:
    sorted_rev = sorted(revenues)
    cutoff = max(1, int(len(sorted_rev) * alpha))
    tail = sorted_rev[:cutoff]
    return statistics.mean(tail) if tail else 0.0


def _select_robust_schedule(revenues: list[float]) -> int:
    if not revenues:
        return 0
    best_idx = 0
    best_score = float("-inf")
    for i, rev in enumerate(revenues):
        cvar = _compute_cvar([rev])
        score = rev - _LAMBDA * abs(min(0.0, cvar))
        if score > best_score:
            best_score = score
            best_idx = i
    return best_idx


def _compute_risque_p95(revenues: list[float], mean_rev: float) -> float:
    if not revenues:
        return 0.0
    sorted_rev = sorted(revenues)
    p5_idx = max(0, int(len(sorted_rev) * 0.05) - 1)
    p5_revenue = sorted_rev[p5_idx]
    return abs(p5_revenue - mean_rev)
