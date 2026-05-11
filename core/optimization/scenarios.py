"""Named scenario definitions for VPP Italia optimisation.

Provides a catalogue of the seven supported scenario types, each wired to its
optimizer class and default parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ScenarioType(str, Enum):
    PEAK_SHAVING = "peak_shaving"
    AUTOCONSOMMATION = "autoconsommation"
    ARBITRAGE_MGP = "arbitrage_mgp"
    FREQUENCY_RESPONSE = "frequency_response"
    PV_BATTERIE = "pv_batterie"
    MULTI_SITE = "multi_site"
    BACKTEST_HISTORIQUE = "backtest_historique"


@dataclass
class ScenarioDefinition:
    """Full description of an optimisation scenario."""

    type: ScenarioType
    name: str
    description: str
    optimizer_class: str
    default_params: dict[str, Any]
    market: str
    tags: list[str] = field(default_factory=list)
    available: bool = True
    future: bool = False


SCENARIOS: dict[ScenarioType, ScenarioDefinition] = {
    ScenarioType.PEAK_SHAVING: ScenarioDefinition(
        type=ScenarioType.PEAK_SHAVING,
        name="Peak Shaving",
        description=(
            "Ecretage des appels de puissance en combinant production PV et stockage batterie "
            "pour reduire la puissance de pointe soutiree du reseau."
        ),
        optimizer_class="PeakShavingOptimizer",
        default_params={
            "puissance_max_kw": 108.0,
            "soc_min_pct": 10.0,
            "soc_max_pct": 90.0,
            "rendement_charge": 0.95,
            "rendement_decharge": 0.95,
        },
        market="interne",
        tags=["pv", "batterie", "reseau"],
        available=True,
    ),
    ScenarioType.AUTOCONSOMMATION: ScenarioDefinition(
        type=ScenarioType.AUTOCONSOMMATION,
        name="Autoconsommation PV+Batterie",
        description=(
            "Maximisation du taux d'autoconsommation photovoltaique grace au stockage batterie : "
            "surplus PV charge, deficit couvert par decharge avant achat reseau."
        ),
        optimizer_class="PeakShavingOptimizer",
        default_params={
            "puissance_max_kw": 108.0,
            "soc_initial_pct": 50.0,
            "soc_min_pct": 10.0,
            "soc_max_pct": 90.0,
        },
        market="interne",
        tags=["pv", "autoconsommation", "batterie"],
        available=True,
    ),
    ScenarioType.ARBITRAGE_MGP: ScenarioDefinition(
        type=ScenarioType.ARBITRAGE_MGP,
        name="Arbitrage MGP",
        description=(
            "Optimisation journaliere buy-low/sell-high sur le marche spot MGP de GME, "
            "enrichie de metriques de risque CVaR et Sharpe."
        ),
        optimizer_class="ArbitrageOptimizer",
        default_params={
            "mode": "standard",
            "soc_initial_pct": 50.0,
        },
        market="MGP",
        tags=["arbitrage", "mgp", "gme", "cvar", "sharpe"],
        available=True,
    ),
    ScenarioType.FREQUENCY_RESPONSE: ScenarioDefinition(
        type=ScenarioType.FREQUENCY_RESPONSE,
        name="Reponse en frequence (FCR/aFRR)",
        description=(
            "Participation aux marches de services systeme Terna (FCR/aFRR) : "
            "fourniture de capacite de regulation primaire et secondaire en frequence."
        ),
        optimizer_class="StochasticOptimizer",
        default_params={
            "n_scenarios": 20,
            "incertitude_pct": 15.0,
        },
        market="MSD",
        tags=["fcr", "afrr", "terna", "frequence"],
        available=False,
        future=True,
    ),
    ScenarioType.PV_BATTERIE: ScenarioDefinition(
        type=ScenarioType.PV_BATTERIE,
        name="Optimisation PV+Batterie couplee",
        description=(
            "Optimisation conjointe de la production PV et du stockage batterie pour maximiser "
            "simultanement l'autoconsommation et les revenus de marche MGP."
        ),
        optimizer_class="ArbitrageOptimizer",
        default_params={
            "mode": "standard",
            "soc_initial_pct": 30.0,
        },
        market="MGP",
        tags=["pv", "batterie", "arbitrage", "hybride"],
        available=True,
    ),
    ScenarioType.MULTI_SITE: ScenarioDefinition(
        type=ScenarioType.MULTI_SITE,
        name="Optimisation multi-sites",
        description=(
            "Coordination de plusieurs sites de stockage pour maximiser les revenus agreges "
            "tout en respectant les contraintes reseau locales de chaque site."
        ),
        optimizer_class="StochasticOptimizer",
        default_params={
            "n_scenarios": 20,
            "incertitude_pct": 20.0,
        },
        market="MGP",
        tags=["multi-site", "agregation", "vpp"],
        available=False,
        future=True,
    ),
    ScenarioType.BACKTEST_HISTORIQUE: ScenarioDefinition(
        type=ScenarioType.BACKTEST_HISTORIQUE,
        name="Backtest historique",
        description=(
            "Simulation retrospective sur donnees MGP historiques pour valider la performance "
            "des strategies d'optimisation sur des periodes passees."
        ),
        optimizer_class="ArbitrageOptimizer",
        default_params={
            "mode": "standard",
        },
        market="MGP",
        tags=["backtest", "historique", "validation"],
        available=True,
    ),
}


def get_scenario(scenario_type: ScenarioType) -> ScenarioDefinition:
    """Return the ScenarioDefinition for the given type."""
    return SCENARIOS[scenario_type]
