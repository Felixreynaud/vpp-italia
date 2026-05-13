"""Background worker that turns dispatch_plans into Huawei commands.

Every ``POLL_INTERVAL_S`` (default 60s), the worker:
1. Computes the current quarter-hour in Europe/Rome (0-95).
2. Loads all DispatchPlan rows for (today, current QH) joined with their
   active Battery.
3. For each Huawei FusionSolar battery, translates ``power_kw`` to a
   charge / discharge / stop command and sends it via HuaweiBatteryClient.
4. Marks the (battery_id, date, QH) as already-applied in memory to avoid
   re-pushing the same setpoint every minute.

This is the production-style scheduler an operator expects: apply a 24h
plan once, and the VPP keeps pushing the correct setpoint every QH for
the next 24h.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from data.models import Battery, BatteryProtocol, DispatchPlan

logger = structlog.get_logger(__name__)

POLL_INTERVAL_S = 60.0
TZ_ROME = ZoneInfo("Europe/Rome")


class DispatchApplier:
    """Tick every minute, apply the QH plan to all Huawei batteries."""

    def __init__(self, session_factory: async_sessionmaker[Any]) -> None:
        self._session_factory = session_factory
        self._running = False
        self._task: asyncio.Task[Any] | None = None
        # Marker: which (battery_id, date_iso, qh) we already pushed,
        # so we don't re-issue the same command every minute.
        self._applied: set[tuple[UUID, str, int]] = set()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="dispatch_applier")
        logger.info("dispatch_applier.started", interval_s=POLL_INTERVAL_S)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("dispatch_applier.stopped")

    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._apply_current_qh()
            except Exception:
                logger.exception("dispatch_applier.cycle_error")
            await asyncio.sleep(POLL_INTERVAL_S)

    async def _apply_current_qh(self) -> None:
        now = datetime.now(TZ_ROME)
        today_iso = now.date().isoformat()
        qh = now.hour * 4 + now.minute // 15

        # Periodically prune stale markers (yesterday's entries)
        self._applied = {m for m in self._applied if m[1] == today_iso}

        async with self._session_factory() as session:
            stmt = (
                select(DispatchPlan, Battery)
                .join(Battery, Battery.battery_id == DispatchPlan.battery_id)
                .where(DispatchPlan.delivery_date == today_iso)
                .where(DispatchPlan.quarter_hour == qh)
                .where(Battery.is_active.is_(True))
            )
            result = await session.execute(stmt)
            rows = list(result.all())

        if not rows:
            return

        logger.debug(
            "dispatch_applier.cycle_start", date=today_iso, qh=qh, candidates=len(rows)
        )

        sent = 0
        for plan, battery in rows:
            marker = (battery.battery_id, today_iso, qh)
            if marker in self._applied:
                continue
            ok = await self._apply_one(battery, plan)
            if ok:
                self._applied.add(marker)
                sent += 1

        if sent:
            logger.info("dispatch_applier.cycle_done", qh=qh, sent=sent)

    async def _apply_one(self, battery: Battery, plan: DispatchPlan) -> bool:
        meta = battery.metadata_ or {}
        subtype = meta.get("subtype")
        if (
            battery.protocol != BatteryProtocol.REST
            or subtype != "huawei_fusion_solar"
        ):
            return False

        from api.routes.batteries import _build_huawei_client

        try:
            client = _build_huawei_client(
                meta["endpoint_url"], meta["client_id"], meta["client_secret"]
            )
            plant_code = meta["plant_code"]

            # Idempotent dispatch-mode activation
            try:
                await client.set_dispatch_mode(plant_code)
            except Exception:
                pass

            power_kw = float(plan.power_kw)
            power_w_abs = abs(power_kw) * 1000.0

            # Convention "batterie" : positive = charge, négative = décharge.
            if power_kw > 0.1:
                await client.charge(plant_code, power_w=power_w_abs)
            elif power_kw < -0.1:
                await client.discharge(plant_code, power_w=power_w_abs)
            else:
                await client.stop(plant_code)

            return True
        except Exception as exc:
            logger.warning(
                "dispatch_applier.apply_failed",
                battery_id=str(battery.battery_id),
                error=str(exc),
            )
            return False
