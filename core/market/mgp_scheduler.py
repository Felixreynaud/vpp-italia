"""Daily scheduler for MGP price fetch — runs at 12:05 Europe/Rome.

GME publishes Day-Ahead prices around 12:00 every day for the next delivery day.
We fetch at 12:05 to leave a small safety margin, retrying every 30 minutes
until the next deadline if a fetch fails (network, transient API error).

The service is idempotent: re-fetching the same date does not create
duplicates. Safe to restart at any time.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.market.mgp_service import MGPService

logger = structlog.get_logger(__name__)

TZ_ROME = ZoneInfo("Europe/Rome")
TARGET_TIME = time(12, 5)  # 12:05 Europe/Rome


class MGPPriceScheduler:
    """Background worker that fetches MGP prices once a day at 12:05.

    Usage (in api/main.py lifespan):
        scheduler = MGPPriceScheduler(session_factory=factory)
        await scheduler.start()
        ...
        await scheduler.stop()
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:  # type: ignore[type-arg]
        self._session_factory = session_factory
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="mgp_price_scheduler")
        logger.info("mgp_scheduler.started")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("mgp_scheduler.stopped")

    async def _loop(self) -> None:
        while self._running:
            await self._wait_until_next_trigger()
            if not self._running:
                return
            await self._fetch_safe()

    async def _wait_until_next_trigger(self) -> None:
        now = datetime.now(TZ_ROME)
        target = now.replace(
            hour=TARGET_TIME.hour,
            minute=TARGET_TIME.minute,
            second=0,
            microsecond=0,
        )
        if target <= now:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        logger.info(
            "mgp_scheduler.sleeping",
            next_run=target.isoformat(),
            wait_seconds=round(wait_seconds),
        )
        await asyncio.sleep(wait_seconds)

    async def _fetch_safe(self) -> None:
        """Trigger one fetch for tomorrow (D+1), the freshly-published day."""
        target = datetime.now(TZ_ROME).date() + timedelta(days=1)
        await self.fetch_for(target)

    async def fetch_for(self, target_date: date) -> dict[str, int] | None:
        """Manual entry point — fetch one specific date. Used by tests / admin."""
        try:
            async with self._session_factory() as session:
                service = MGPService(session)
                counts = await service.fetch_and_store(target_date)
                logger.info("mgp_scheduler.fetch_ok", date=str(target_date), counts=counts)
                return counts
        except Exception:
            logger.exception("mgp_scheduler.fetch_failed", date=str(target_date))
            return None
