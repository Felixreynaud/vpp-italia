"""High-level MGP price service — fetch all zones + PUN, persist idempotently.

Wraps `GMEPriceClient` (which is per-zone) into a fleet-level service that
iterates over the 7 Italian Day-Ahead zones + PUN, normalises the data and
upserts into the `mgp_prices` table.

The insert is idempotent: re-fetching the same date is safe and does not
create duplicates (we SELECT existing keys first then INSERT only what's new).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.market.gme_client import GMEPriceClient
from data.models import MGPPrice, MGPZone

logger = structlog.get_logger(__name__)

TZ_ROME = ZoneInfo("Europe/Rome")

# 7 Italian zones (no PUN here — PUN is the national average, fetched separately
# because the mercati-energetici library exposes it as a daily index rather than
# 24 hourly values).
_ZONES = [
    MGPZone.NORD,
    MGPZone.CNOR,
    MGPZone.CSUD,
    MGPZone.SUD,
    MGPZone.CALA,
    MGPZone.SARD,
    MGPZone.SICI,
]


def today_rome() -> date:
    return datetime.now(TZ_ROME).date()


class MGPService:
    """Service-level facade over GMEPriceClient.

    Usage:
        async with session_factory() as session:
            service = MGPService(session)
            counts = await service.fetch_and_store(target_date)
            # counts = {"NORD": 24, "CNOR": 24, ..., "PUN": 24}
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_and_store(self, target_date: date) -> dict[str, int]:
        """Fetch all 7 zones + PUN for `target_date` and persist.

        Returns the number of rows actually inserted per zone (may be 0
        if the zone is already in DB or if the API returned nothing).
        """
        date_str = target_date.isoformat()
        rows: list[dict[str, Any]] = []
        counts: dict[str, int] = {}

        for zone in _ZONES:
            client = GMEPriceClient(zone=zone.value)
            try:
                prices = await client.get_mgp_prices(target_date)
            except Exception as exc:
                logger.warning(
                    "mgp_service.zone_fetch_failed",
                    zone=zone.value,
                    date=date_str,
                    error=str(exc),
                )
                counts[zone.value] = 0
                continue

            for hour, price in prices.items():
                rows.append(
                    {
                        "delivery_date": date_str,
                        "hour": int(hour),
                        "zone": zone,
                        "price_eur_mwh": Decimal(str(round(float(price), 2))),
                    }
                )
            counts[zone.value] = len(prices)

        # PUN — national average. Library exposes a single daily value, so we
        # replicate it across the 24 hours for storage consistency.
        try:
            pun_client = GMEPriceClient(zone=MGPZone.NORD.value)
            pun_value = await pun_client.get_pun_index(target_date)
            if pun_value > 0:
                for hour in range(24):
                    rows.append(
                        {
                            "delivery_date": date_str,
                            "hour": hour,
                            "zone": MGPZone.PUN,
                            "price_eur_mwh": Decimal(str(round(float(pun_value), 2))),
                        }
                    )
                counts[MGPZone.PUN.value] = 24
            else:
                counts[MGPZone.PUN.value] = 0
        except Exception as exc:
            logger.warning("mgp_service.pun_fetch_failed", date=date_str, error=str(exc))
            counts[MGPZone.PUN.value] = 0

        inserted = await self._upsert_rows(rows, date_str)
        logger.info(
            "mgp_service.fetched",
            date=date_str,
            fetched=sum(counts.values()),
            inserted=inserted,
        )
        return counts

    async def backfill(self, days: int) -> dict[str, Any]:
        """Backfill the last `days` days (excluding today, since intraday).

        Typical first-deployment call: `await service.backfill(days=90)`.
        Idempotent — safe to re-run.
        """
        today = today_rome()
        summary: dict[str, int] = {}
        for offset in range(1, days + 1):
            target = today - timedelta(days=offset)
            try:
                counts = await self.fetch_and_store(target)
                summary[target.isoformat()] = sum(counts.values())
            except Exception:
                logger.exception("mgp_service.backfill_day_failed", date=str(target))
                summary[target.isoformat()] = 0
        logger.info(
            "mgp_service.backfill_done",
            days=days,
            total_rows=sum(summary.values()),
        )
        return {"days_processed": days, "summary": summary}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _upsert_rows(self, rows: list[dict[str, Any]], date_str: str) -> int:
        """Insert rows skipping duplicates on (delivery_date, hour, zone).

        Portable approach: SELECT existing keys for this date, filter new rows
        in-memory, bulk INSERT. Acceptable because we fetch at most 192 rows
        per call (24 hours × 8 zones) once a day.
        """
        if not rows:
            return 0

        existing = await self._session.execute(
            select(MGPPrice.hour, MGPPrice.zone).where(MGPPrice.delivery_date == date_str)
        )
        existing_keys = {(int(h), z) for h, z in existing}

        new_rows = [r for r in rows if (r["hour"], r["zone"]) not in existing_keys]
        if not new_rows:
            return 0

        self._session.add_all([MGPPrice(**r) for r in new_rows])
        await self._session.commit()
        return len(new_rows)
