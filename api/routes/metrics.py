"""Métriques et health check — endpoints pour Grafana et monitoring."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Response

router = APIRouter(tags=["monitoring"])
logger = structlog.get_logger(__name__)

TZ_ROME = ZoneInfo("Europe/Rome")

_start_time = time.monotonic()


# ---------------------------------------------------------------------------
# Health check enrichi
# ---------------------------------------------------------------------------


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """Health check enrichi — retourne l'état de l'API et des batteries en ligne."""
    uptime_seconds = int(time.monotonic() - _start_time)
    now = datetime.now(TZ_ROME)

    # Tentative de récupération du nombre de batteries en ligne
    batteries_online = 0
    batteries_total = 0
    db_ok = False

    try:
        from api.dependencies import _session_factory  # type: ignore[attr-defined]

        if _session_factory:
            from sqlalchemy import func, select

            from data.models import Battery, BatteryState

            async with _session_factory() as session:
                total_res = await session.execute(select(func.count()).select_from(Battery))
                batteries_total = total_res.scalar_one_or_none() or 0

                online_res = await session.execute(
                    select(func.count())
                    .select_from(Battery)
                    .where(
                        Battery.state.in_(
                            [BatteryState.ONLINE, BatteryState.CHARGING, BatteryState.DISCHARGING]
                        )
                    )
                )
                batteries_online = online_res.scalar_one_or_none() or 0
                db_ok = True
    except Exception as exc:
        logger.warning("health.db_check_failed", error=str(exc))

    return {
        "status": "ok",
        "version": "0.1.0",
        "environment": os.getenv("APP_ENV", "development"),
        "timestamp": now.isoformat(),
        "uptime_seconds": uptime_seconds,
        "batteries_online": batteries_online,
        "batteries_total": batteries_total,
        "database": "ok" if db_ok else "degraded",
    }


# ---------------------------------------------------------------------------
# Métriques Prometheus par batterie
# ---------------------------------------------------------------------------


@router.get("/metrics/batteries", response_class=Response)
async def battery_metrics() -> Response:
    """Métriques Prometheus pour les batteries — format texte standard.

    Utilisé par Grafana Agent ou Prometheus scrape.
    Expose soc_percent, power_kw, temperature_c, status par batterie.
    """
    lines: list[str] = []

    try:
        from sqlalchemy import select, text

        from api.dependencies import _session_factory  # type: ignore[attr-defined]
        from data.models import Battery, BatteryState

        if _session_factory is None:
            raise RuntimeError("DB not ready")

        async with _session_factory() as session:
            batteries_res = await session.execute(
                select(Battery).where(Battery.is_active == True)  # noqa: E712
            )
            batteries = batteries_res.scalars().all()

            # Fetch latest reading per battery via TimescaleDB DISTINCT ON
            readings_sql = text("""
                SELECT DISTINCT ON (battery_id)
                    battery_id,
                    soc_percent,
                    power_kw,
                    temperature_c,
                    voltage_v,
                    state,
                    time
                FROM battery_readings
                WHERE time > NOW() - INTERVAL '10 minutes'
                ORDER BY battery_id, time DESC
            """)
            readings_res = await session.execute(readings_sql)
            readings = {str(r.battery_id): r for r in readings_res}

        # HELP and TYPE lines
        lines += [
            "# HELP vpp_battery_soc_percent State of Charge in percent (0-100)",
            "# TYPE vpp_battery_soc_percent gauge",
            "# HELP vpp_battery_power_kw Active power in kW (positive=charge, negative=discharge)",
            "# TYPE vpp_battery_power_kw gauge",
            "# HELP vpp_battery_temperature_celsius Cell temperature in Celsius",
            "# TYPE vpp_battery_temperature_celsius gauge",
            "# HELP vpp_battery_online 1 if battery is reachable, 0 otherwise",
            "# TYPE vpp_battery_online gauge",
            "# HELP vpp_battery_voltage_volts Pack voltage in V",
            "# TYPE vpp_battery_voltage_volts gauge",
        ]

        online_states = {
            BatteryState.ONLINE,
            BatteryState.CHARGING,
            BatteryState.DISCHARGING,
            BatteryState.IDLE,
        }

        for bat in batteries:
            bid = str(bat.battery_id)
            labels = f'battery_id="{bid}",asset_id="{bat.asset_id}",protocol="{bat.protocol.value}"'
            reading = readings.get(bid)

            is_online = int(bat.state in online_states)
            lines.append(f"vpp_battery_online{{{labels}}} {is_online}")

            if reading:
                if reading.soc_percent is not None:
                    lines.append(
                        f"vpp_battery_soc_percent{{{labels}}} {float(reading.soc_percent):.2f}"
                    )
                if reading.power_kw is not None:
                    lines.append(f"vpp_battery_power_kw{{{labels}}} {float(reading.power_kw):.3f}")
                if reading.temperature_c is not None:
                    lines.append(
                        f"vpp_battery_temperature_celsius{{{labels}}} {float(reading.temperature_c):.1f}"
                    )
                if reading.voltage_v is not None:
                    lines.append(
                        f"vpp_battery_voltage_volts{{{labels}}} {float(reading.voltage_v):.1f}"
                    )

    except Exception as exc:
        logger.warning("metrics.battery_scrape_failed", error=str(exc))
        lines.append(f"# ERROR: {exc}")

    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


# ---------------------------------------------------------------------------
# P&L temps réel
# ---------------------------------------------------------------------------


@router.get("/metrics/pnl")
async def pnl_metrics() -> dict[str, Any]:
    """P&L réalisé et projeté — aujourd'hui, semaine, mois.

    Calcule le revenu d'arbitrage MGP à partir des logs de dispatch.
    """
    now = datetime.now(TZ_ROME)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now.weekday())
    month_start = today_start.replace(day=1)

    today_eur = 0.0
    week_eur = 0.0
    month_eur = 0.0
    projected_today_eur = 0.0

    try:
        from sqlalchemy import text

        from api.dependencies import _session_factory  # type: ignore[attr-defined]

        if _session_factory:
            async with _session_factory() as session:
                # Requires dispatch_logs table (created by DispatchScheduler)
                pnl_sql = text("""
                    SELECT
                        SUM(CASE WHEN timestamp >= :today  THEN revenue_eur ELSE 0 END) AS today_eur,
                        SUM(CASE WHEN timestamp >= :week   THEN revenue_eur ELSE 0 END) AS week_eur,
                        SUM(CASE WHEN timestamp >= :month  THEN revenue_eur ELSE 0 END) AS month_eur
                    FROM dispatch_logs
                    WHERE success = true
                      AND timestamp >= :month
                """)
                result = await session.execute(
                    pnl_sql,
                    {
                        "today": today_start.isoformat(),
                        "week": week_start.isoformat(),
                        "month": month_start.isoformat(),
                    },
                )
                row = result.fetchone()
                if row:
                    today_eur = float(row.today_eur or 0)
                    week_eur = float(row.week_eur or 0)
                    month_eur = float(row.month_eur or 0)

                # Projected from the dispatch scheduler if available
                from api.main import _scheduler  # type: ignore[attr-defined]

                if _scheduler and hasattr(_scheduler, "get_today_pnl"):
                    pnl_data = _scheduler.get_today_pnl()
                    projected_today_eur = pnl_data.get("projected_pnl_eur", 0.0)

    except Exception as exc:
        logger.warning("metrics.pnl_query_failed", error=str(exc))

    return {
        "data": {
            "today_eur": round(today_eur, 2),
            "week_eur": round(week_eur, 2),
            "month_eur": round(month_eur, 2),
            "projected_today_eur": round(projected_today_eur, 2),
            "currency": "EUR",
            "timestamp": now.isoformat(),
        },
        "meta": {
            "today_start": today_start.isoformat(),
            "week_start": week_start.isoformat(),
            "month_start": month_start.isoformat(),
        },
    }
