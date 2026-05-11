"""Real-time dispatch scheduler — ties GME prices, optimizer, and Huawei client together.

Runs as a long-lived asyncio background task. At 13:30 CET it fetches the next
day's MGP prices, runs the optimizer, and stores the daily schedule. At the top
of each hour it sends the corresponding dispatch commands to all batteries via
the Huawei NBI connector. A watchdog monitors live SoC and adjusts if the actual
state deviates more than SOC_DEVIATION_THRESHOLD % from the plan.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from core.dispatch.models import (
    ActionType,
    BatterySpec,
    DailySchedule,
    DispatchLog,
    ScheduleStatus,
)
from core.dispatch.optimizer import DispatchOptimizer
from core.market.gme_client import GMEPriceClient

logger = structlog.get_logger(__name__)

TZ_ROME = ZoneInfo("Europe/Rome")
PRICE_FETCH_TIME = time(13, 30)  # 13:30 CET — 30 min after GME publication
SOC_DEVIATION_THRESHOLD = 5.0  # Percent — triggers re-plan if exceeded


class DispatchScheduler:
    """Background scheduler that automates the full dispatch cycle.

    Lifecycle:
        scheduler = DispatchScheduler(batteries, gme_client, huawei_client)
        await scheduler.start()   # non-blocking, spawns tasks
        ...
        await scheduler.stop()
    """

    def __init__(
        self,
        batteries: list[BatterySpec],
        gme_client: GMEPriceClient,
        huawei_client: Any,  # HuaweiBatteryClient | HuaweiSimulator
        optimizer: DispatchOptimizer | None = None,
        zone: str | None = None,
    ) -> None:
        self._batteries = batteries
        self._battery_map = {b.battery_id: b for b in batteries}
        self._gme = gme_client
        self._huawei = huawei_client
        self._optimizer = optimizer or DispatchOptimizer()
        self._zone = (zone or os.getenv("GME_ZONE", "SUD")).upper()

        self._schedules: dict[date, DailySchedule] = {}
        self._dispatch_logs: list[DispatchLog] = []
        self._tasks: list[asyncio.Task] = []
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        self._tasks = [
            asyncio.create_task(self._price_fetch_loop(), name="dispatch.price_fetch"),
            asyncio.create_task(self._hourly_dispatch_loop(), name="dispatch.hourly"),
            asyncio.create_task(self._soc_watchdog_loop(), name="dispatch.soc_watchdog"),
        ]
        logger.info("dispatch_scheduler.started", batteries=len(self._batteries))

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("dispatch_scheduler.stopped")

    # ------------------------------------------------------------------
    # Daily price fetch + optimization (13:30 CET)
    # ------------------------------------------------------------------

    async def _price_fetch_loop(self) -> None:
        while self._running:
            await self._wait_until(PRICE_FETCH_TIME)
            if not self._running:
                break
            try:
                await self._fetch_and_optimize()
            except Exception:
                logger.exception("dispatch_scheduler.price_fetch_error")

    async def _fetch_and_optimize(self, target: date | None = None) -> DailySchedule:
        tomorrow = target or (datetime.now(TZ_ROME).date() + timedelta(days=1))

        logger.info("dispatch_scheduler.fetching_prices", date=str(tomorrow))
        prices = await self._gme.get_mgp_prices(tomorrow)

        schedule = self._optimizer.optimize_day(
            prices_24h=prices,
            batteries=self._batteries,
            delivery_date=tomorrow,
            zone=self._zone,
        )
        self._schedules[tomorrow] = schedule

        logger.info(
            "dispatch_scheduler.schedule_ready",
            date=str(tomorrow),
            pnl_eur=round(schedule.estimated_pnl_eur, 2),
            batteries=len(self._batteries),
        )
        return schedule

    # ------------------------------------------------------------------
    # Hourly dispatch execution
    # ------------------------------------------------------------------

    async def _hourly_dispatch_loop(self) -> None:
        while self._running:
            now = datetime.now(TZ_ROME)
            # Sleep until the top of the next hour (plus a small margin)
            next_hour = now.replace(minute=0, second=5, microsecond=0) + timedelta(hours=1)
            wait_seconds = (next_hour - now).total_seconds()
            await asyncio.sleep(wait_seconds)

            if not self._running:
                break

            current_hour = datetime.now(TZ_ROME).hour
            today = datetime.now(TZ_ROME).date()
            schedule = self._schedules.get(today)

            if not schedule:
                logger.warning(
                    "dispatch_scheduler.no_schedule", date=str(today), hour=current_hour
                )
                continue

            try:
                await self._execute_hour(schedule, current_hour)
            except Exception:
                logger.exception(
                    "dispatch_scheduler.hourly_dispatch_error", hour=current_hour
                )

    async def _execute_hour(self, schedule: DailySchedule, hour: int) -> None:
        hourly = schedule.hours.get(hour)
        if not hourly:
            return

        schedule.status = ScheduleStatus.EXECUTING

        for battery_id, action in hourly.actions.items():
            battery = self._battery_map.get(battery_id)
            if not battery:
                continue

            try:
                if action.action_type == ActionType.CHARGE:
                    task = await self._huawei.charge(battery_id, power_w=action.power_kw * 1000)
                elif action.action_type == ActionType.DISCHARGE:
                    task = await self._huawei.discharge(
                        battery_id, power_w=abs(action.power_kw) * 1000
                    )
                else:
                    task = await self._huawei.stop(battery_id)

                log_entry = DispatchLog(
                    timestamp=datetime.now(TZ_ROME),
                    battery_id=battery_id,
                    hour=hour,
                    planned_action=action.action_type,
                    planned_power_kw=action.power_kw,
                    actual_power_kw=action.power_kw,  # confirmed on task completion
                    planned_soc_pct=action.soc_before_pct,
                    actual_soc_pct=action.soc_before_pct,
                    soc_deviation_pct=0.0,
                    price_eur_mwh=action.target_price_eur_mwh,
                    revenue_eur=action.estimated_revenue_eur,
                    success=True,
                )
                self._dispatch_logs.append(log_entry)

                logger.info(
                    "dispatch_scheduler.command_sent",
                    battery_id=battery_id,
                    hour=hour,
                    action=action.action_type.value,
                    power_kw=action.power_kw,
                    price_eur_mwh=action.target_price_eur_mwh,
                    request_id=getattr(task, "request_id", None),
                )

            except Exception as exc:
                logger.error(
                    "dispatch_scheduler.command_failed",
                    battery_id=battery_id,
                    hour=hour,
                    action=action.action_type.value,
                    error=str(exc),
                )
                # Fail-safe: attempt to stop the battery
                try:
                    await self._huawei.stop(battery_id)
                except Exception:
                    logger.critical(
                        "dispatch_scheduler.stop_failed", battery_id=battery_id, hour=hour
                    )

    # ------------------------------------------------------------------
    # SoC watchdog (every 10 minutes)
    # ------------------------------------------------------------------

    async def _soc_watchdog_loop(self) -> None:
        while self._running:
            await asyncio.sleep(600)
            if not self._running:
                break
            try:
                await self._check_soc_deviations()
            except Exception:
                logger.exception("dispatch_scheduler.watchdog_error")

    async def _check_soc_deviations(self) -> None:
        today = datetime.now(TZ_ROME).date()
        current_hour = datetime.now(TZ_ROME).hour
        schedule = self._schedules.get(today)
        if not schedule:
            return

        for battery in self._batteries:
            planned_hour = schedule.hours.get(current_hour)
            if not planned_hour:
                continue
            planned_action = planned_hour.actions.get(battery.battery_id)
            if not planned_action:
                continue

            # Fetch actual SoC via Huawei real-time API
            try:
                devices = await self._huawei.get_device_list(battery.battery_id)
                if not devices:
                    continue
                statuses = await self._huawei.get_battery_realtime(
                    [d.device_id for d in devices], plant_code=battery.battery_id
                )
                if not statuses:
                    continue
                actual_soc = statuses[0].soc
            except Exception:
                continue

            deviation = abs(actual_soc - planned_action.soc_before_pct)
            if deviation > SOC_DEVIATION_THRESHOLD:
                logger.warning(
                    "dispatch_scheduler.soc_deviation",
                    battery_id=battery.battery_id,
                    planned_soc=planned_action.soc_before_pct,
                    actual_soc=actual_soc,
                    deviation=round(deviation, 1),
                )
                # Re-optimize the remaining hours with actual SoC
                await self._replan_remaining(battery, actual_soc, today, current_hour)

    async def _replan_remaining(
        self, battery: BatterySpec, actual_soc: float, target_date: date, from_hour: int
    ) -> None:
        schedule = self._schedules.get(target_date)
        if not schedule:
            return

        # Build remaining prices for hours from_hour..23
        prices = {h: schedule.hours[h].hour_price_eur_mwh for h in range(from_hour, 24)}
        updated_battery = BatterySpec(
            battery_id=battery.battery_id,
            capacity_kwh=battery.capacity_kwh,
            max_power_kw=battery.max_power_kw,
            soc_min_pct=battery.soc_min_pct,
            soc_max_pct=battery.soc_max_pct,
            initial_soc_pct=actual_soc,
            efficiency_roundtrip=battery.efficiency_roundtrip,
            ramp_kw_per_min=battery.ramp_kw_per_min,
        )
        new_sched = self._optimizer.optimize_day(
            prices_24h=prices,
            batteries=[updated_battery],
            delivery_date=target_date,
            zone=self._zone,
        )
        # Patch remaining hours in the live schedule
        for h in range(from_hour, 24):
            if h in new_sched.hours and battery.battery_id in new_sched.hours[h].actions:
                schedule.hours[h].actions[battery.battery_id] = new_sched.hours[h].actions[
                    battery.battery_id
                ]

        logger.info(
            "dispatch_scheduler.replanned",
            battery_id=battery.battery_id,
            from_hour=from_hour,
            actual_soc=actual_soc,
        )

    # ------------------------------------------------------------------
    # Public queries
    # ------------------------------------------------------------------

    def get_schedule(self, target_date: date | None = None) -> DailySchedule | None:
        key = target_date or datetime.now(TZ_ROME).date()
        return self._schedules.get(key)

    def get_today_pnl(self) -> dict[str, float]:
        today = datetime.now(TZ_ROME).date()
        current_hour = datetime.now(TZ_ROME).hour
        schedule = self._schedules.get(today)
        if not schedule:
            return {"realised_pnl_eur": 0.0, "projected_pnl_eur": 0.0}

        realised = sum(
            a.estimated_revenue_eur
            for h, hs in schedule.hours.items()
            if h < current_hour
            for a in hs.actions.values()
        )
        projected = schedule.estimated_pnl_eur
        return {
            "realised_pnl_eur": round(realised, 2),
            "projected_pnl_eur": round(projected, 2),
            "completion_pct": round(current_hour / 24 * 100, 1),
        }

    def get_recent_logs(self, n: int = 50) -> list[DispatchLog]:
        return self._dispatch_logs[-n:]

    async def force_schedule(self, schedule: DailySchedule) -> None:
        """Override the optimizer output with a manually built schedule."""
        schedule.status = ScheduleStatus.OVERRIDDEN
        self._schedules[schedule.date] = schedule
        logger.warning("dispatch_scheduler.schedule_forced", date=str(schedule.date))

    # ------------------------------------------------------------------
    # Trigger on-demand (for API endpoint)
    # ------------------------------------------------------------------

    async def trigger_now(self, delivery_date: date | None = None) -> DailySchedule:
        """Fetch prices and compute schedule immediately (API-triggered)."""
        return await self._fetch_and_optimize(target=delivery_date)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _wait_until(target: time) -> None:
        now = datetime.now(TZ_ROME)
        next_run = now.replace(
            hour=target.hour, minute=target.minute, second=0, microsecond=0
        )
        if next_run <= now:
            next_run += timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
