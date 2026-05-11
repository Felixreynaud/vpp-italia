"""Market window scheduler — triggers optimization and offer submission on time."""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import structlog

logger = structlog.get_logger(__name__)

TZ_ROME = ZoneInfo("Europe/Rome")


class MarketScheduler:
    """Schedules recurring tasks aligned with Italian electricity market windows."""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._tasks = [
            asyncio.create_task(self._run_mgp_scheduler(), name="mgp_scheduler"),
            asyncio.create_task(self._run_msd_scheduler(), name="msd_scheduler"),
            asyncio.create_task(self._run_telemetry_watchdog(), name="telemetry_watchdog"),
        ]
        logger.info("scheduler.started")

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("scheduler.stopped")

    async def _run_mgp_scheduler(self) -> None:
        """MGP: daily at 11:55 CET — submit D+1 offers before 12:00 deadline."""
        while self._running:
            await self._wait_until_next(time(11, 55), tz=TZ_ROME)
            logger.info("scheduler.mgp_window_open")
            try:
                from core.optimizer import run_optimization_async

                task_id = await run_optimization_async()
                logger.info("scheduler.mgp_optimization_triggered", task_id=task_id)
            except Exception:
                logger.exception("scheduler.mgp_error")

    async def _run_msd_scheduler(self) -> None:
        """MSD ex-ante: daily at 17:55 CET — submit capacity offers for D+1."""
        while self._running:
            await self._wait_until_next(time(17, 55), tz=TZ_ROME)
            logger.info("scheduler.msd_window_open")
            try:
                await self._submit_msd_offers()
            except Exception:
                logger.exception("scheduler.msd_error")

    async def _run_telemetry_watchdog(self) -> None:
        """Every 30s: check for batteries that haven't reported — trigger safe state."""
        while self._running:
            await asyncio.sleep(30)
            try:
                await self._check_stale_batteries()
            except Exception:
                logger.exception("scheduler.watchdog_error")

    async def _submit_msd_offers(self) -> None:
        logger.info("scheduler.msd_offer_submission_started")
        # Full implementation: fetch optimized plans, build MSD offer payload, call TernaClient

    async def _check_stale_batteries(self) -> None:
        logger.debug("scheduler.watchdog_check")
        # Full implementation: query last telemetry timestamps, trigger safe state via DispatchExecutor

    @staticmethod
    async def _wait_until_next(target_time: time, tz: ZoneInfo) -> None:
        """Sleep until the next occurrence of target_time in the given timezone."""
        now = datetime.now(tz)
        target = now.replace(
            hour=target_time.hour,
            minute=target_time.minute,
            second=0,
            microsecond=0,
        )
        if target <= now:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        logger.debug(
            "scheduler.next_trigger",
            target=target.isoformat(),
            wait_seconds=round(wait_seconds),
        )
        await asyncio.sleep(wait_seconds)
