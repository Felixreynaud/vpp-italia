"""Tests for core.optimization modules."""

from __future__ import annotations

from uuid import uuid4

import pytest

from core.dispatch.models import BatterySpec
from core.optimization.arbitrage import ArbitrageInput, ArbitrageOptimizer
from core.optimization.peak_shaving import PeakShavingInput, PeakShavingOptimizer
from core.optimization.stochastic import StochasticInput, StochasticOptimizer


def _make_battery(
    capacity_kwh: float = 500.0,
    max_power_kw: float = 108.0,
    soc_pct: float = 50.0,
) -> BatterySpec:
    return BatterySpec(
        battery_id=str(uuid4()),
        capacity_kwh=capacity_kwh,
        max_power_kw=max_power_kw,
        initial_soc_pct=soc_pct,
    )


def test_autoconsommation_rate() -> None:
    pv = [100.0] * 24
    conso = [50.0] * 24
    prix = [60.0] * 24
    inp = PeakShavingInput(
        production_pv_kw=pv,
        consommation_site_kw=conso,
        prix_mgp=prix,
        soc_initial_pct=50.0,
        capacite_kwh=500.0,
    )
    result = PeakShavingOptimizer().optimize(inp)
    assert result.taux_autoconsommation_pct > 70.0


def test_peak_shaving_soc_bounds() -> None:
    inp = PeakShavingInput(
        production_pv_kw=[80.0] * 24,
        consommation_site_kw=[60.0] * 24,
        prix_mgp=[55.0] * 24,
        soc_initial_pct=50.0,
        capacite_kwh=300.0,
        soc_min_pct=10.0,
        soc_max_pct=90.0,
    )
    result = PeakShavingOptimizer().optimize(inp)
    for soc in result.soc_evolution_pct:
        assert 9.9 <= soc <= 90.1, f"SoC {soc}% out of bounds"


def test_peak_shaving_schedule_length() -> None:
    inp = PeakShavingInput(
        production_pv_kw=[50.0] * 24,
        consommation_site_kw=[50.0] * 24,
        prix_mgp=[60.0] * 24,
        soc_initial_pct=50.0,
        capacite_kwh=200.0,
    )
    result = PeakShavingOptimizer().optimize(inp)
    assert len(result.schedule_kw) == 24
    assert len(result.soc_evolution_pct) == 25


def test_peak_shaving_no_pv_grid_purchase() -> None:
    inp = PeakShavingInput(
        production_pv_kw=[0.0] * 24,
        consommation_site_kw=[30.0] * 24,
        prix_mgp=[60.0] * 24,
        soc_initial_pct=10.0,
        capacite_kwh=200.0,
    )
    result = PeakShavingOptimizer().optimize(inp)
    assert all(g >= 0.0 for g in result.achat_reseau_kw)
    assert sum(result.achat_reseau_kw) > 0


def test_peak_shaving_economie_positive_when_surplus() -> None:
    inp = PeakShavingInput(
        production_pv_kw=[120.0] * 12 + [0.0] * 12,
        consommation_site_kw=[80.0] * 24,
        prix_mgp=[70.0] * 24,
        soc_initial_pct=10.0,
        capacite_kwh=500.0,
    )
    result = PeakShavingOptimizer().optimize(inp)
    assert result.economie_estimee_eur >= 0.0


def test_arbitrage_revenu_positif() -> None:
    prix_mgp_1mars_2025 = [
        45, 40, 38, 36, 35, 38, 55, 75, 90, 88, 82, 78,
        70, 65, 68, 72, 85, 95, 100, 92, 80, 70, 60, 50,
    ]
    battery = _make_battery(capacity_kwh=500.0, max_power_kw=108.0, soc_pct=50.0)
    inp = ArbitrageInput(prix_mgp=prix_mgp_1mars_2025, batteries=[battery])
    result = ArbitrageOptimizer().optimize(inp)
    assert result.revenu_estime_eur > 0


def test_arbitrage_sharpe_and_cvar_are_floats() -> None:
    prix = [60.0] * 8 + [90.0] * 8 + [50.0] * 8
    battery = _make_battery()
    inp = ArbitrageInput(prix_mgp=prix, batteries=[battery])
    result = ArbitrageOptimizer().optimize(inp)
    assert isinstance(result.sharpe_ratio, float)
    assert isinstance(result.cvar_95_eur, float)
    assert result.cvar_95_eur >= 0.0


def test_arbitrage_modes() -> None:
    prix = [50.0 + i * 2 for i in range(24)]
    battery = _make_battery()
    for mode in ("conservateur", "standard", "agressif"):
        inp = ArbitrageInput(prix_mgp=prix, batteries=[battery], mode=mode)
        result = ArbitrageOptimizer().optimize(inp)
        assert result.schedule is not None


def test_arbitrage_replan_with_actuals() -> None:
    prix_full = [60.0] * 24
    battery = _make_battery()
    opt = ArbitrageOptimizer()
    result = opt.replan_with_actuals(
        current_soc=55.0,
        elapsed_hours=8,
        actual_prices=prix_full[8:],
        batteries=[battery],
    )
    assert result.schedule is not None


def test_stochastique_robustesse() -> None:
    prix_base = [60.0] * 24
    battery = _make_battery()
    inp = StochasticInput(
        prix_mgp_base=prix_base,
        batteries=[battery],
        n_scenarios=20,
        random_seed=42,
    )
    result = StochasticOptimizer().optimize(inp)
    assert len(result.scenarios_revenus) == 20
    assert result.schedule_robuste is not None


def test_stochastique_revenu_espere_non_negative() -> None:
    prix_base = [60.0] * 24
    battery = _make_battery()
    inp = StochasticInput(
        prix_mgp_base=prix_base,
        batteries=[battery],
        n_scenarios=20,
        random_seed=42,
    )
    result = StochasticOptimizer().optimize(inp)
    assert result.revenu_espere_eur >= 0


def test_stochastique_risque_non_negative() -> None:
    prix_base = [40.0 + i for i in range(24)]
    battery = _make_battery()
    inp = StochasticInput(
        prix_mgp_base=prix_base,
        batteries=[battery],
        n_scenarios=10,
        random_seed=0,
    )
    result = StochasticOptimizer().optimize(inp)
    assert result.risque_p95_eur >= 0.0


def test_stochastique_seed_reproducibility() -> None:
    prix_base = [55.0] * 24
    battery = _make_battery()

    def run(seed: int) -> list[float]:
        inp = StochasticInput(prix_mgp_base=prix_base, batteries=[battery], n_scenarios=10, random_seed=seed)
        return StochasticOptimizer().optimize(inp).scenarios_revenus

    assert run(7) == run(7)


def test_scenarios_liste() -> None:
    from core.optimization.scenarios import SCENARIOS, ScenarioType
    assert len(SCENARIOS) == 7
    assert ScenarioType.PEAK_SHAVING in SCENARIOS


def test_all_scenario_types_present() -> None:
    from core.optimization.scenarios import SCENARIOS, ScenarioType
    for st in ScenarioType:
        assert st in SCENARIOS, f"Missing scenario definition for {st}"


def test_get_scenario_returns_correct_type() -> None:
    from core.optimization.scenarios import ScenarioType, get_scenario
    sc = get_scenario(ScenarioType.ARBITRAGE_MGP)
    assert sc.type == ScenarioType.ARBITRAGE_MGP
    assert sc.optimizer_class == "ArbitrageOptimizer"
    assert sc.market == "MGP"


def test_scenario_available_flags() -> None:
    from core.optimization.scenarios import ScenarioType, get_scenario
    assert get_scenario(ScenarioType.PEAK_SHAVING).available is True
    assert get_scenario(ScenarioType.ARBITRAGE_MGP).available is True
    assert get_scenario(ScenarioType.FREQUENCY_RESPONSE).future is True
