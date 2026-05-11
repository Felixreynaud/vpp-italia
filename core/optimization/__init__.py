"""Optimization modules for VPP Italia.

Exposes:
- PeakShavingOptimizer: PV + battery self-consumption optimizer
- ArbitrageOptimizer: CVaR/Sharpe-enriched MGP arbitrage
- StochasticOptimizer: Scenario-based robust optimization
- SCENARIOS / ScenarioType: Named scenario catalogue
"""

from __future__ import annotations

from core.optimization.arbitrage import ArbitrageInput, ArbitrageOptimizer, ArbitrageResult
from core.optimization.peak_shaving import (
    PeakShavingInput,
    PeakShavingOptimizer,
    PeakShavingResult,
)
from core.optimization.scenarios import SCENARIOS, ScenarioDefinition, ScenarioType, get_scenario
from core.optimization.stochastic import StochasticInput, StochasticOptimizer, StochasticResult

__all__ = [
    "PeakShavingInput",
    "PeakShavingOptimizer",
    "PeakShavingResult",
    "ArbitrageInput",
    "ArbitrageOptimizer",
    "ArbitrageResult",
    "StochasticInput",
    "StochasticOptimizer",
    "StochasticResult",
    "SCENARIOS",
    "ScenarioDefinition",
    "ScenarioType",
    "get_scenario",
]
