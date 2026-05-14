"""Endpoints stub utilisés par le frontend (dashboard, history, marchés).

Ces endpoints agrègent les données réelles quand disponibles (batteries,
readings) et renvoient des mocks plausibles quand la donnée n'existe pas
encore en BDD (prix MGP par exemple — pas encore branché sur GME).
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter
from sqlalchemy import func, select, text

from api.dependencies import CurrentUser, DbSession
from data.models import Battery, BatteryState

router = APIRouter()
logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# /metrics/fleet — KPIs agrégés pour le Dashboard
# ---------------------------------------------------------------------------


@router.get("/metrics/fleet")
async def fleet_metrics(db: DbSession, _user: CurrentUser) -> dict[str, Any]:
    """Aggregate fleet KPIs — restricted to managed (is_active=true) batteries.

    Portfolio-only batteries (is_active=false) are catalogued but not yet
    operated by the VPP, so they're excluded from dashboard KPIs.
    """
    total_res = await db.execute(
        select(func.count()).select_from(Battery).where(Battery.is_active.is_(True))
    )
    batteries_total = int(total_res.scalar_one_or_none() or 0)

    active_states = (
        BatteryState.IDLE,
        BatteryState.CHARGING,
        BatteryState.DISCHARGING,
    )
    active_res = await db.execute(
        select(func.count())
        .select_from(Battery)
        .where(Battery.is_active.is_(True))
        .where(Battery.state.in_(active_states))
    )
    batteries_actives = int(active_res.scalar_one_or_none() or 0)

    cap_res = await db.execute(
        select(func.coalesce(func.sum(Battery.capacity_kwh), 0)).where(Battery.is_active.is_(True))
    )
    total_capacity_kwh = float(cap_res.scalar_one_or_none() or 0)

    soc_moyen = 0.0
    puissance_totale_kw = 0.0
    try:
        # Last reading per battery — average SoC + sum of power, restricted to managed batteries
        agg_sql = text(
            """
            WITH latest AS (
                SELECT DISTINCT ON (r.battery_id)
                    r.battery_id, r.soc_percent, r.power_kw, r.time
                FROM battery_readings r
                JOIN batteries b ON b.battery_id = r.battery_id
                WHERE r.time > NOW() - INTERVAL '10 minutes'
                  AND b.is_active = true
                ORDER BY r.battery_id, r.time DESC
            )
            SELECT
                COALESCE(AVG(soc_percent), 0) AS soc_avg,
                COALESCE(SUM(power_kw), 0)    AS power_sum
            FROM latest
            """
        )
        agg_res = await db.execute(agg_sql)
        row = agg_res.fetchone()
        if row:
            soc_moyen = float(row.soc_avg or 0)
            puissance_totale_kw = float(row.power_sum or 0)
    except Exception as exc:
        logger.warning("dashboard.fleet_agg_failed", error=str(exc))

    # Available energy = sum(capacity * soc / 100) — quick proxy in MWh
    energie_disponible_mwh = round(total_capacity_kwh * soc_moyen / 100 / 1000, 2)

    # Convention exposée : positive = charge, négative = décharge — identique
    # à la convention native Huawei stockée dans battery_readings.
    return {
        "data": {
            "soc_moyen": round(soc_moyen, 1),
            "puissance_totale_kw": round(puissance_totale_kw, 1),
            "batteries_actives": batteries_actives,
            "batteries_total": batteries_total,
            "energie_disponible_mwh": energie_disponible_mwh,
            # P&L mock — pas encore branché sur dispatch_logs réels
            "pnl_jour_eur": round(280 + random.gauss(0, 50), 2),
        },
        "meta": {"timestamp": datetime.now(UTC).isoformat()},
    }


# ---------------------------------------------------------------------------
# /markets/mgp/prices — courbe MGP du jour (mock plausible en attendant GME)
# ---------------------------------------------------------------------------


@router.get("/markets/mgp/prices")
async def mgp_prices_today(_user: CurrentUser) -> dict[str, Any]:
    """Courbe MGP horaire du jour — mock réaliste : creux nocturnes + pics 18-21h."""
    # Base curve (€/MWh) typique italienne — creux à 3-5h, pic à 18-20h
    base_curve = [
        45,
        42,
        40,
        38,
        37,
        38,
        50,
        75,
        90,
        85,
        78,
        72,
        68,
        65,
        70,
        80,
        95,
        110,
        105,
        92,
        78,
        65,
        55,
        48,
    ]
    prices = [
        {"hour": h, "price_eur_mwh": round(p + random.gauss(0, 3), 2)}
        for h, p in enumerate(base_curve)
    ]
    return {
        "data": {"prices": prices},
        "meta": {
            "timestamp": datetime.now(UTC).isoformat(),
            "source": "mock-pending-gme-integration",
        },
    }


# ---------------------------------------------------------------------------
# /history — séries temporelles consolidées pour la page Historique
# ---------------------------------------------------------------------------


@router.get("/history")
async def fleet_history(db: DbSession, _user: CurrentUser) -> dict[str, Any]:
    """168 h glissantes d'historique flotte — power charge/discharge + SoC moyen.

    Pour chaque heure, agrège les readings (sum power positive/négative,
    moyenne SoC). Si peu de data en BDD (moins d'une heure de polling),
    complète avec une projection lissée pour ne pas afficher un graphique vide.
    """
    points: list[dict[str, Any]] = []
    try:
        history_sql = text(
            """
            SELECT
                date_trunc('hour', time) AS hour,
                COALESCE(SUM(CASE WHEN power_kw > 0 THEN power_kw ELSE 0 END), 0) AS charge_kw,
                COALESCE(SUM(CASE WHEN power_kw < 0 THEN -power_kw ELSE 0 END), 0) AS discharge_kw,
                COALESCE(AVG(soc_percent), 0) AS soc_avg
            FROM battery_readings
            WHERE time > NOW() - INTERVAL '7 days'
            GROUP BY hour
            ORDER BY hour
            """
        )
        result = await db.execute(history_sql)
        for row in result:
            points.append(
                {
                    "timestamp": row.hour.isoformat()
                    if row.hour
                    else datetime.now(UTC).isoformat(),
                    "power_charge_kw": round(float(row.charge_kw or 0), 1),
                    "power_discharge_kw": round(float(row.discharge_kw or 0), 1),
                    "soc_moyen": round(float(row.soc_avg or 0), 1),
                    "pnl_cumul_eur": 0.0,
                }
            )
    except Exception as exc:
        logger.warning("dashboard.history_agg_failed", error=str(exc))

    # Backfill mock if we don't have at least 24 points yet
    if len(points) < 24:
        now = datetime.now(UTC)
        for i in range(168):
            ts = now - timedelta(hours=167 - i)
            hour = ts.hour
            discharge = 200 + 100 * math.sin((hour - 18) / 3) if 16 <= hour <= 22 else 0
            charge = 150 + 50 * math.sin((hour - 3) / 3) if 1 <= hour <= 6 else 0
            points.append(
                {
                    "timestamp": ts.isoformat(),
                    "power_charge_kw": round(max(0, charge + random.gauss(0, 20)), 1),
                    "power_discharge_kw": round(max(0, discharge + random.gauss(0, 30)), 1),
                    "soc_moyen": round(45 + 20 * math.sin(i / 12) + random.gauss(0, 3), 1),
                    "pnl_cumul_eur": round(i * 25 + random.gauss(0, 15), 2),
                }
            )

    return {
        "data": points,
        "meta": {"count": len(points), "timestamp": datetime.now(UTC).isoformat()},
    }


@router.get("/history/sessions")
async def history_sessions(_user: CurrentUser) -> dict[str, Any]:
    """Dernières sessions de dispatch — mock en attendant dispatch_logs réels."""
    markets = ["MSD", "MGP", "MI1", "MI3", "MB"]
    now = datetime.now(UTC)
    sessions = [
        {
            "id": f"sess-{i + 1:04d}",
            "date": (now - timedelta(days=i)).isoformat(),
            "duration_min": 15 + (i * 7) % 90,
            "energie_mwh": round(0.5 + (i % 9) * 0.6, 2),
            "revenu_eur": round(80 + (i * 23) % 400, 2),
            "marche": markets[i % len(markets)],
        }
        for i in range(20)
    ]
    return {
        "data": sessions,
        "meta": {"count": len(sessions), "source": "mock-pending-dispatch-logs"},
    }
