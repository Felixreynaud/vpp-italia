"""Background battery polling.

Every ``POLL_INTERVAL_S`` seconds, this worker queries each active battery
via its configured connector (currently Huawei FusionSolar over REST) and
persists a ``BatteryReading`` row in TimescaleDB. The latest known state
is also pushed back to the ``Battery.state`` column so the frontend list
shows charging / discharging / idle / offline in near real-time.

Failures are logged and the battery is marked OFFLINE, but the poller
itself keeps running.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from data.models import Battery, BatteryProtocol, BatteryReading, BatteryState

logger = structlog.get_logger(__name__)

POLL_INTERVAL_S = 10.0


class BatteryPoller:
    def __init__(self, session_factory: async_sessionmaker[Any]) -> None:
        self._session_factory = session_factory
        self._running = False
        self._task: asyncio.Task[Any] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="battery_poller")
        logger.info("battery_poller.started", interval_s=POLL_INTERVAL_S)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("battery_poller.stopped")

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._poll_all()
            except Exception:
                logger.exception("battery_poller.cycle_error")
            await asyncio.sleep(POLL_INTERVAL_S)

    async def _poll_all(self) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(Battery).where(Battery.is_active.is_(True))
            )
            batteries = list(result.scalars().all())

        if not batteries:
            return

        results = await asyncio.gather(
            *(self._poll_one(b) for b in batteries),
            return_exceptions=True,
        )
        ok = sum(1 for r in results if r is True)
        logger.debug("battery_poller.cycle_done", total=len(batteries), ok=ok)

    # ------------------------------------------------------------------
    # Single battery
    # ------------------------------------------------------------------

    async def _poll_one(self, battery: Battery) -> bool:
        meta: dict[str, Any] = battery.metadata_ or {}
        if (
            battery.protocol != BatteryProtocol.REST
            or meta.get("subtype") != "huawei_fusion_solar"
        ):
            return False  # protocol not handled yet

        # Lazy import to avoid pulling FastAPI into core/ at module load
        from api.routes.batteries import _build_huawei_client

        try:
            client = _build_huawei_client(
                meta["endpoint_url"], meta["client_id"], meta["client_secret"]
            )
            statuses = await client.get_battery_realtime(
                device_ids=[meta["device_id"]], plant_code=meta["plant_code"]
            )
        except Exception as exc:
            logger.warning(
                "battery_poller.fetch_failed",
                battery_id=str(battery.battery_id),
                error=str(exc),
            )
            await self._update_state(battery.battery_id, BatteryState.OFFLINE)
            return False

        if not statuses:
            await self._update_state(battery.battery_id, BatteryState.OFFLINE)
            return False

        s = statuses[0]
        if s.power_kw > 0.1:
            state = BatteryState.CHARGING
        elif s.power_kw < -0.1:
            state = BatteryState.DISCHARGING
        else:
            state = BatteryState.IDLE

        await self._persist_reading(battery.battery_id, s, state)
        return True

    async def _persist_reading(
        self, battery_id: UUID, status: Any, state: BatteryState
    ) -> None:
        async with self._session_factory() as session:
            reading = BatteryReading(
                time=datetime.now(UTC),
                battery_id=battery_id,
                soc_percent=_dec(status.soc),
                power_kw=_dec(status.power_kw),
                voltage_v=_dec(status.voltage_v),
                current_a=_dec(status.current_a),
                temperature_c=_dec(status.temperature_c),
                state=state,
                raw=status.model_dump(mode="json"),
            )
            session.add(reading)

            db_battery = await session.get(Battery, battery_id)
            if db_battery is not None:
                db_battery.state = state

            await session.commit()

    async def _update_state(self, battery_id: UUID, state: BatteryState) -> None:
        async with self._session_factory() as session:
            db_battery = await session.get(Battery, battery_id)
            if db_battery is not None and db_battery.state != state:
                db_battery.state = state
                await session.commit()


def _dec(value: float | int | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))
