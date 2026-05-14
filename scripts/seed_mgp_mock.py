"""Seed mgp_prices with realistic mock Italian Day-Ahead prices.

Generates plausible hourly prices for all 7 zones + PUN over N days,
calibrated on observed 2024-2025 Italian market patterns:

- Typical intra-day shape: night trough (3-5h), morning ramp (7-11h),
  midday PV-induced dip (12-15h), evening peak (18-21h).
- Inter-zone spreads: SUD/CALA cheaper (producing south),
  CSUD/SARD slightly above NORD, SICI noticeably higher (island,
  limited transmission), PUN as national weighted average.
- Weekday vs weekend: weekend ~10 % cheaper on average.
- Small per-hour gaussian noise for realism.

Deterministic by default (seed=42) so re-running gives the same data.

Usage:
    python -m scripts.seed_mgp_mock                  # 90 days, no clear
    python -m scripts.seed_mgp_mock --days 30
    python -m scripts.seed_mgp_mock --clear          # wipe first
    python -m scripts.seed_mgp_mock --days 365 --seed 7
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from data.models import Base, MGPPrice, MGPZone

logger = structlog.get_logger(__name__)

# Italian Day-Ahead market — typical hourly shape (€/MWh) for NORD zone.
# Calibrated on 2024-2025 observed patterns: troughs at 3-5h, morning
# ramp 7-11h, PV-induced midday dip, sharp evening peak around 19-20h.
_BASE_CURVE_NORD: tuple[float, ...] = (
    52.0,  # 00h
    48.0,
    44.0,
    42.0,  # 03h — night trough
    41.0,
    44.0,
    55.0,
    78.0,  # 07h — morning ramp
    92.0,
    88.0,
    80.0,
    74.0,
    68.0,  # 12h — midday PV dip
    65.0,
    68.0,
    76.0,
    88.0,
    105.0,
    118.0,  # 18h — evening peak
    108.0,
    92.0,
    75.0,
    62.0,
    55.0,  # 23h
)

# Inter-zone spreads (€/MWh) added to NORD value.
_ZONE_OFFSET: dict[MGPZone, float] = {
    MGPZone.NORD: 0.0,
    MGPZone.CNOR: 2.0,  # close to NORD, slightly higher
    MGPZone.CSUD: 5.5,  # consuming central region
    MGPZone.SUD: -3.0,  # producing southern region — often cheaper
    MGPZone.CALA: -2.5,  # similar to SUD
    MGPZone.SARD: 8.0,  # Sardinia island — limited transmission
    MGPZone.SICI: 15.0,  # Sicily island — often markedly higher
}

# PUN (national weighted average) is roughly NORD + a small premium.
_PUN_OFFSET = 1.5

# Noise parameters.
_HOUR_NOISE_STD = 3.0  # €/MWh, per-hour intra-day noise on NORD value
_ZONE_NOISE_STD = 1.5  # €/MWh, per-zone independent noise
_PUN_NOISE_STD = 1.0
_DAILY_NOISE_STD = 0.07  # 7 % relative daily multiplier
_WEEKEND_FACTOR = 0.92  # weekends ~8 % cheaper on average


def generate_day(target_date: date, rng: random.Random) -> list[dict[str, Any]]:
    """Return 192 rows (24h × 8 zones) for `target_date` with realistic prices."""
    is_weekend = target_date.weekday() >= 5
    weekend_multi = _WEEKEND_FACTOR if is_weekend else 1.0
    daily_multi = weekend_multi * (1.0 + rng.gauss(0, _DAILY_NOISE_STD))

    rows: list[dict[str, Any]] = []
    for hour in range(24):
        nord_price = _BASE_CURVE_NORD[hour] * daily_multi + rng.gauss(0, _HOUR_NOISE_STD)
        nord_price = max(5.0, nord_price)  # price floor — never negative

        # Seven physical zones
        for zone in (
            MGPZone.NORD,
            MGPZone.CNOR,
            MGPZone.CSUD,
            MGPZone.SUD,
            MGPZone.CALA,
            MGPZone.SARD,
            MGPZone.SICI,
        ):
            price = nord_price + _ZONE_OFFSET[zone] + rng.gauss(0, _ZONE_NOISE_STD)
            price = max(5.0, price)
            rows.append(
                {
                    "delivery_date": target_date.isoformat(),
                    "hour": hour,
                    "zone": zone,
                    "price_eur_mwh": Decimal(str(round(price, 2))),
                }
            )

        # PUN — national average
        pun_price = nord_price + _PUN_OFFSET + rng.gauss(0, _PUN_NOISE_STD)
        pun_price = max(5.0, pun_price)
        rows.append(
            {
                "delivery_date": target_date.isoformat(),
                "hour": hour,
                "zone": MGPZone.PUN,
                "price_eur_mwh": Decimal(str(round(pun_price, 2))),
            }
        )
    return rows


async def run(days: int, clear: bool, seed: int) -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("seed_mgp_mock.missing_database_url")
        return 2

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        if clear:
            logger.info("seed_mgp_mock.clearing_existing_rows")
            await session.execute(delete(MGPPrice))
            await session.commit()

        rng = random.Random(seed)
        rome = ZoneInfo("Europe/Rome")
        today = datetime.now(rome).date()

        total_rows = 0
        for offset in range(1, days + 1):
            target = today - timedelta(days=offset)
            rows = generate_day(target, rng)
            session.add_all([MGPPrice(**r) for r in rows])
            await session.commit()
            total_rows += len(rows)
            if offset % 10 == 0:
                logger.info("seed_mgp_mock.progress", days_done=offset, rows_total=total_rows)

        logger.info("seed_mgp_mock.done", days=days, total_rows=total_rows)

    await engine.dispose()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed realistic mock MGP prices")
    parser.add_argument("--days", type=int, default=90, help="Days to seed (default 90)")
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Wipe mgp_prices table before seeding (recommended on first run)",
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="RNG seed for reproducibility (default 42)"
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.days, args.clear, args.seed)))


if __name__ == "__main__":
    main()
