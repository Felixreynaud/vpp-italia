"""PV + battery self-consumption (peak-shaving) optimizer.

Pure-Python greedy algorithm — no external solver required.
Processes each hour sequentially, tracking SoC and grid flows.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PeakShavingInput:
    """Inputs for a 24-hour peak-shaving optimisation."""

    production_pv_kw: list[float]     # 24 hourly values
    consommation_site_kw: list[float]  # 24 hourly values
    prix_mgp: list[float]              # €/MWh, 24 hourly values
    soc_initial_pct: float             # 0–100
    capacite_kwh: float
    puissance_max_kw: float = 108.0    # LUNA2000 constraint
    soc_min_pct: float = 10.0
    soc_max_pct: float = 90.0
    rendement_charge: float = 0.95
    rendement_decharge: float = 0.95


@dataclass
class PeakShavingResult:
    """Outputs of a 24-hour peak-shaving run."""

    schedule_kw: list[float]           # 24 values (+charge, -decharge)
    soc_evolution_pct: list[float]     # 25 values (initial + one per hour)
    surplus_pv_kw: list[float]         # PV energy not used or stored
    achat_reseau_kw: list[float]       # Grid purchases needed each hour
    taux_autoconsommation_pct: float   # % of PV consumed locally
    economie_estimee_eur: float        # vs baseline (no battery)
    metadata: dict = field(default_factory=dict)


class PeakShavingOptimizer:
    """Greedy self-consumption optimizer for PV + battery systems."""

    def optimize(self, inp: PeakShavingInput) -> PeakShavingResult:
        """Run the 24-hour greedy self-consumption schedule.

        Args:
            inp: PeakShavingInput with PV, load, price, and battery parameters.

        Returns:
            PeakShavingResult with schedule, SoC trajectory, and KPIs.
        """
        soc = inp.soc_initial_pct
        schedule: list[float] = []
        soc_evolution: list[float] = [soc]
        surplus_pv: list[float] = []
        achat_reseau: list[float] = []
        achat_baseline: list[float] = []

        for h in range(24):
            pv = inp.production_pv_kw[h]
            load = inp.consommation_site_kw[h]
            surplus = pv - load

            # Baseline: how much grid purchase without battery
            baseline_grid = max(0.0, load - pv)
            achat_baseline.append(baseline_grid)

            power_kw, soc = self._dispatch_hour(inp, soc, surplus)
            schedule.append(power_kw)
            soc_evolution.append(soc)

            # After battery dispatch, compute residual flows
            net_after_battery = surplus - power_kw  # positive = still surplus
            grid_purchase = max(0.0, -net_after_battery)
            spill = max(0.0, net_after_battery)

            surplus_pv.append(spill)
            achat_reseau.append(grid_purchase)

        taux = _compute_taux_autoconso(
            inp.production_pv_kw,
            achat_reseau,
            achat_baseline,
        )
        economie = _compute_economie(achat_baseline, achat_reseau, inp.prix_mgp)

        return PeakShavingResult(
            schedule_kw=schedule,
            soc_evolution_pct=soc_evolution,
            surplus_pv_kw=surplus_pv,
            achat_reseau_kw=achat_reseau,
            taux_autoconsommation_pct=round(taux, 2),
            economie_estimee_eur=round(economie, 4),
            metadata={
                "total_pv_kwh": round(sum(inp.production_pv_kw), 2),
                "total_conso_kwh": round(sum(inp.consommation_site_kw), 2),
                "total_achat_reseau_kwh": round(sum(achat_reseau), 2),
                "prix_moyen_eur_mwh": round(statistics.mean(inp.prix_mgp), 2),
            },
        )

    def _dispatch_hour(
        self,
        inp: PeakShavingInput,
        soc: float,
        surplus_kw: float,
    ) -> tuple[float, float]:
        if surplus_kw > 0:
            return self._charge(inp, soc, surplus_kw)
        if surplus_kw < 0:
            return self._discharge(inp, soc, abs(surplus_kw))
        return 0.0, soc

    def _charge(
        self,
        inp: PeakShavingInput,
        soc: float,
        available_kw: float,
    ) -> tuple[float, float]:
        if soc >= inp.soc_max_pct:
            return 0.0, soc
        headroom_kwh = (inp.soc_max_pct - soc) / 100.0 * inp.capacite_kwh
        max_charge_kwh = inp.puissance_max_kw * inp.rendement_charge
        charge_kwh = min(available_kw * inp.rendement_charge, max_charge_kwh, headroom_kwh)
        if charge_kwh <= 0.0:
            return 0.0, soc
        power_kw = min(charge_kwh / inp.rendement_charge, inp.puissance_max_kw)
        new_soc = soc + (charge_kwh / inp.capacite_kwh) * 100.0
        return round(power_kw, 3), round(min(new_soc, inp.soc_max_pct), 4)

    def _discharge(
        self,
        inp: PeakShavingInput,
        soc: float,
        needed_kw: float,
    ) -> tuple[float, float]:
        if soc <= inp.soc_min_pct:
            return 0.0, soc
        available_kwh = (soc - inp.soc_min_pct) / 100.0 * inp.capacite_kwh
        max_discharge_kwh = inp.puissance_max_kw / inp.rendement_decharge
        discharge_kwh = min(needed_kw / inp.rendement_decharge, max_discharge_kwh, available_kwh)
        if discharge_kwh <= 0.0:
            return 0.0, soc
        power_kw = min(discharge_kwh * inp.rendement_decharge, inp.puissance_max_kw)
        new_soc = soc - (discharge_kwh / inp.capacite_kwh) * 100.0
        return round(-power_kw, 3), round(max(new_soc, inp.soc_min_pct), 4)


def _compute_taux_autoconso(
    pv_kw: list[float],
    achat_reseau: list[float],
    achat_baseline: list[float],
) -> float:
    total_pv = sum(pv_kw)
    if total_pv <= 0:
        return 0.0
    total_baseline_grid = sum(achat_baseline)
    total_actual_grid = sum(achat_reseau)
    pv_direct_to_load = total_pv - total_baseline_grid
    pv_via_battery = total_baseline_grid - total_actual_grid
    total_pv_consumed = max(0.0, pv_direct_to_load + pv_via_battery)
    return min(100.0, (total_pv_consumed / total_pv) * 100.0)


def _compute_economie(
    achat_baseline: list[float],
    achat_reseau: list[float],
    prix_mgp: list[float],
) -> float:
    return sum(
        (b - a) * p / 1000.0
        for b, a, p in zip(achat_baseline, achat_reseau, prix_mgp)
    )
