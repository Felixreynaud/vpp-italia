"""GME price client — wraps the `mercati-energetici` open-source library.

pip install mercati-energetici

Data availability:
  MGP   : published daily ~13:00 CET for the next delivery day
  MI-A1 : ~17:30 CET same day
  MI-A2 : ~22:30 CET same day
  MI-A3 : ~07:30 CET delivery day
  MI-A4 : ~11:30 CET delivery day
  MI-A5 : ~14:00 CET delivery day
  MI-A6 : ~16:00 CET delivery day
  MI-A7 : ~18:00 CET delivery day

Zones: NORD | CNORD | CSUD | SUD | SICI | SARD
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from core.dispatch.models import HourlyPrice

logger = structlog.get_logger(__name__)

TZ_ROME = ZoneInfo("Europe/Rome")

# Zone name mapping from GME API format to our internal format
GME_ZONE_MAP = {
    "NORD": "NORD",
    "CNORD": "CNORD",
    "CSUD": "CSUD",
    "SUD": "SUD",
    "SICI": "SICI",
    "SARD": "SARD",
    # API may return these variants
    "Centro-Nord": "CNORD",
    "Centro-Sud": "CSUD",
    "Sud": "SUD",
    "Sicilia": "SICI",
    "Sardegna": "SARD",
}


def _today_rome() -> date:
    return datetime.now(TZ_ROME).date()


class GMEPriceClient:
    """Retrieves MGP and MI prices from GME via the mercati-energetici library.

    Responses are cached in memory. The optional TimescaleDB session is used
    for longer-term persistence so the API is not called redundantly across
    restarts.

    Fallback: if the GME API is unavailable, the previous day's prices are
    returned with a warning. This prevents the optimizer from blocking.
    """

    def __init__(
        self,
        zone: str | None = None,
        db_session: Any = None,
    ) -> None:
        self._zone = (zone or os.getenv("GME_ZONE", "SUD")).upper()
        self._db = db_session
        self._cache: dict[str, list[HourlyPrice]] = {}  # key → prices

        # Lazy-import the library so the rest of the codebase loads without it
        try:
            import mercati_energetici as _me  # noqa: F401
            self._lib_available = True
        except ImportError:
            logger.warning("gme.library_not_installed", hint="pip install mercati-energetici")
            self._lib_available = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def get_mgp_prices(self, delivery_date: date | None = None) -> dict[int, float]:
        """Return {hour(0-23): price_eur_mwh} for the given delivery date.

        Defaults to tomorrow (D+1), the typical day-ahead schedule.
        Falls back to yesterday's prices if the API is unavailable.
        """
        target = delivery_date or (_today_rome() + timedelta(days=1))
        cache_key = f"mgp:{target}:{self._zone}"

        if cache_key in self._cache:
            return self._prices_to_dict(self._cache[cache_key])

        # Try DB cache first
        cached = await self._load_from_db(cache_key)
        if cached:
            self._cache[cache_key] = cached
            return self._prices_to_dict(cached)

        # Fetch from GME API
        try:
            prices = await self._fetch_mgp(target)
        except Exception as exc:
            logger.warning("gme.mgp_api_unavailable", date=str(target), error=str(exc))
            prices = await self._fallback_prices(target, market="MGP")

        self._cache[cache_key] = prices
        await self._save_to_db(cache_key, prices)
        return self._prices_to_dict(prices)

    async def get_pun_index(self, target_date: date | None = None) -> float:
        """Return the daily PUN (Prezzo Unico Nazionale) average in EUR/MWh."""
        target = target_date or _today_rome()
        try:
            prices = await self.get_mgp_prices(target)
            if prices:
                return sum(prices.values()) / len(prices)
        except Exception as exc:
            logger.warning("gme.pun_error", date=str(target), error=str(exc))
        return 0.0

    async def get_mi_prices(self, session: str = "MI-A1", target_date: date | None = None) -> dict[int, float]:
        """Return intraday prices for a given MI session.

        session: MI-A1 … MI-A7
        """
        target = target_date or _today_rome()
        cache_key = f"{session}:{target}:{self._zone}"

        if cache_key in self._cache:
            return self._prices_to_dict(self._cache[cache_key])

        try:
            prices = await self._fetch_mi(target, session)
        except Exception as exc:
            logger.warning("gme.mi_api_unavailable", session=session, date=str(target), error=str(exc))
            # Fall back to MGP for this hour range
            mgp = await self.get_mgp_prices(target)
            return mgp

        self._cache[cache_key] = prices
        return self._prices_to_dict(prices)

    async def get_zone_prices(
        self, zone: str | None = None, delivery_date: date | None = None
    ) -> dict[int, float]:
        """Return MGP prices for the specified zone (defaults to configured zone)."""
        original_zone = self._zone
        if zone:
            self._zone = zone.upper()
        try:
            return await self.get_mgp_prices(delivery_date)
        finally:
            self._zone = original_zone

    # ------------------------------------------------------------------
    # Fetch helpers
    # ------------------------------------------------------------------

    async def _fetch_mgp(self, target: date) -> list[HourlyPrice]:
        """Call the mercati-energetici library for MGP prices."""
        if not self._lib_available:
            raise RuntimeError("mercati-energetici library not installed")

        from mercati_energetici import MGP  # type: ignore[import]

        mgp = MGP()
        # The library returns a list of dicts with keys varying by version;
        # we handle both the legacy and current format.
        raw: list[dict] = await asyncio.to_thread(mgp.get_prices, target)

        return self._parse_raw_prices(raw, market="MGP", target=target)

    async def _fetch_mi(self, target: date, session: str) -> list[HourlyPrice]:
        if not self._lib_available:
            raise RuntimeError("mercati-energetici library not installed")

        from mercati_energetici import MI  # type: ignore[import]

        # MI sessions are named MI-A1..MI-A7; the library may use MIA1..MIA7
        session_code = session.replace("-", "")
        mi = MI(session=session_code)
        raw: list[dict] = await asyncio.to_thread(mi.get_prices, target)
        return self._parse_raw_prices(raw, market=session, target=target)

    def _parse_raw_prices(
        self, raw: list[dict], market: str, target: date
    ) -> list[HourlyPrice]:
        """Normalise the library's raw response into HourlyPrice objects.

        The library may return hour as 1-24 (Italian convention) or 0-23.
        We normalise to 0-23.
        """
        prices: list[HourlyPrice] = []
        for entry in raw:
            # Extract zone price — try several possible key names
            zone_key = self._zone
            price_raw = (
                entry.get(zone_key)
                or entry.get(zone_key.lower())
                or entry.get("PUN")
                or entry.get("price")
                or entry.get("prezzo")
            )
            if price_raw is None:
                continue

            # Normalise hour
            hour_raw = int(entry.get("ora") or entry.get("hour") or entry.get("Ora") or 1)
            hour = (hour_raw - 1) % 24  # Italian GME hours are 1-based

            prices.append(
                HourlyPrice(
                    hour=hour,
                    price_eur_mwh=float(price_raw),
                    zone=self._zone,
                    market=market,
                    date=target,
                )
            )

        # Sort by hour and deduplicate (keep last entry for DST days)
        seen: dict[int, HourlyPrice] = {}
        for p in sorted(prices, key=lambda x: x.hour):
            seen[p.hour] = p
        return list(seen.values())

    async def _fallback_prices(self, target: date, market: str) -> list[HourlyPrice]:
        """Return previous day's prices as fallback when API is down."""
        yesterday = target - timedelta(days=1)
        cache_key = f"{market.lower()}:{yesterday}:{self._zone}"
        if cache_key in self._cache:
            logger.warning("gme.using_fallback_prices", target=str(target), fallback=str(yesterday))
            return [
                HourlyPrice(hour=p.hour, price_eur_mwh=p.price_eur_mwh, zone=p.zone, market=p.market, date=target)
                for p in self._cache[cache_key]
            ]
        # No fallback available — return flat estimate
        logger.error("gme.no_fallback_available", target=str(target))
        return [
            HourlyPrice(hour=h, price_eur_mwh=80.0, zone=self._zone, market=market, date=target)
            for h in range(24)
        ]

    # ------------------------------------------------------------------
    # DB cache (optional — only used if db_session is provided)
    # ------------------------------------------------------------------

    async def _load_from_db(self, cache_key: str) -> list[HourlyPrice] | None:
        if self._db is None:
            return None
        try:
            from sqlalchemy import text

            result = await self._db.execute(
                text("SELECT hour, price_eur_mwh, zone, market FROM gme_price_cache WHERE cache_key = :k"),
                {"k": cache_key},
            )
            rows = result.fetchall()
            if not rows:
                return None
            return [
                HourlyPrice(hour=r.hour, price_eur_mwh=r.price_eur_mwh, zone=r.zone, market=r.market)
                for r in rows
            ]
        except Exception:
            return None

    async def _save_to_db(self, cache_key: str, prices: list[HourlyPrice]) -> None:
        if self._db is None or not prices:
            return
        try:
            from sqlalchemy import text

            for p in prices:
                await self._db.execute(
                    text("""
                        INSERT INTO gme_price_cache (cache_key, hour, price_eur_mwh, zone, market)
                        VALUES (:k, :h, :p, :z, :m)
                        ON CONFLICT (cache_key, hour) DO UPDATE SET price_eur_mwh = EXCLUDED.price_eur_mwh
                    """),
                    {"k": cache_key, "h": p.hour, "p": p.price_eur_mwh, "z": p.zone, "m": p.market},
                )
            await self._db.commit()
        except Exception as exc:
            logger.warning("gme.db_save_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _prices_to_dict(prices: list[HourlyPrice]) -> dict[int, float]:
        return {p.hour: p.price_eur_mwh for p in prices}

    def invalidate_cache(self, target: date | None = None) -> None:
        if target is None:
            self._cache.clear()
        else:
            keys_to_del = [k for k in self._cache if str(target) in k]
            for k in keys_to_del:
                del self._cache[k]
